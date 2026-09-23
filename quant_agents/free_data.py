from __future__ import annotations

import json
import math
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig
from .market_sources import MootdxKlineClient, SourceDataError
from .models import StockSnapshot
from .universe import is_allowed_code
from .utils import rank01


class FreeAshareProvider:
    """Free A-share data provider backed by public Eastmoney HTTP endpoints.

    The provider intentionally avoids third-party dependencies so that the
    simulation can run before AKShare/pandas are installed. Raw responses are
    cached locally to make later backtests reproducible.
    """

    spot_url = "https://push2.eastmoney.com/api/qt/clist/get"
    kline_url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    yahoo_chart_url = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    min_spot_cache_rows = 1_000

    def __init__(
        self,
        cache_dir: Path | str = Path("data/cache/free"),
        config: AppConfig | None = None,
        request_pause: float = 0.20,
        kline_source: str = "eastmoney",
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.config = config or AppConfig()
        self.request_pause = request_pause
        self.kline_source = kline_source
        self.mootdx = MootdxKlineClient()
        self.last_stats: dict[str, int] = {}
        self.last_failed_codes: list[str] = []
        self.last_insufficient_codes: list[str] = []
        self.source_stats: dict[str, int] = {"mootdx_ok": 0, "mootdx_failed": 0, "eastmoney_ok": 0}

    def load_snapshots(
        self,
        trade_date: date | None = None,
        lookback_days: int = 120,
        limit: int = 100,
        offset: int = 0,
        adjust: str = "qfq",
        use_cache: bool = True,
    ) -> list[StockSnapshot]:
        day = trade_date or date.today()
        spot_rows = self.fetch_spot(day, use_cache=use_cache)
        candidates = self._select_candidates(spot_rows, limit, offset)
        snapshots = self.load_snapshots_from_rows(candidates, day, lookback_days, adjust=adjust, use_cache=use_cache)
        self.last_stats = {
            **self.last_stats,
            "spot_rows": len(spot_rows),
            "offset": offset,
            "limit": limit,
        }
        return snapshots

    def load_snapshots_from_rows(
        self,
        candidates: list[dict[str, Any]],
        day: date,
        lookback_days: int = 120,
        adjust: str = "qfq",
        use_cache: bool = True,
    ) -> list[StockSnapshot]:
        self.source_stats = {"mootdx_ok": 0, "mootdx_failed": 0, "eastmoney_ok": 0}
        metrics: dict[str, dict[str, Any]] = {}
        start = day - timedelta(days=max(lookback_days * 2, 160))
        failed = 0
        insufficient = 0
        failed_codes: list[str] = []
        insufficient_codes: list[str] = []
        for row in candidates:
            code = str(row.get("f12", ""))
            try:
                bars = self.fetch_kline(code, start, day, adjust=adjust, use_cache=use_cache)
            except FreeDataError:
                failed += 1
                failed_codes.append(code)
                continue
            item = self._compute_metrics(row, bars, day)
            if item:
                metrics[code] = item
            else:
                insufficient += 1
                insufficient_codes.append(code)
            time.sleep(self.request_pause)

        self.last_failed_codes = failed_codes
        self.last_insufficient_codes = insufficient_codes

        self.last_stats = {
            "candidates": len(candidates),
            "snapshots": len(metrics),
            "failed_kline": failed,
            "insufficient_history": insufficient,
            **self.source_stats,
        }

        relative_rank = rank01({code: item["return_20d"] for code, item in metrics.items()}, reverse=True)
        pe_rank = _pe_ranks(metrics)
        snapshots: list[StockSnapshot] = []
        for code, item in metrics.items():
            snapshots.append(
                StockSnapshot(
                    code=code,
                    name=item["name"],
                    trade_date=item["trade_date"],
                    open_price=item["open_price"],
                    close=item["close"],
                    prev_close=item["prev_close"],
                    ma20=item["ma20"],
                    ma60=item["ma60"],
                    ma60_slope=item["ma60_slope"],
                    return_20d=item["return_20d"],
                    return_60d=item["return_60d"],
                    volatility_20d=item["volatility_20d"],
                    avg_turnover_20d=item["avg_turnover_20d"],
                    turnover_rate_20d=item["turnover_rate_20d"],
                    relative_strength=relative_rank.get(code, 0.5),
                    listing_days=item["listing_days"],
                    is_st=item["is_st"],
                    is_suspended=item["is_suspended"],
                    is_limit_up=item["is_limit_up"],
                    is_limit_down=item["is_limit_down"],
                    limit_up_price=item["limit_up_price"],
                    limit_down_price=item["limit_down_price"],
                    is_data_stale=item["is_data_stale"],
                    pe_rank=pe_rank.get(code, 0.5),
                )
            )
        return snapshots

    def eligible_candidates(self, trade_date: date | None = None, use_cache: bool = True) -> list[dict[str, Any]]:
        day = trade_date or date.today()
        rows = self.fetch_spot(day, use_cache=use_cache)
        return self._select_candidates(rows, limit=1_000_000, offset=0)

    def fetch_spot(self, trade_date: date, use_cache: bool = True) -> list[dict[str, Any]]:
        cache_path = self.cache_dir / "spot_all" / f"{trade_date:%Y%m%d}.json"
        legacy_cache_path = self.cache_dir / "spot" / f"{trade_date:%Y%m%d}.json"
        if use_cache:
            cached_rows = self._read_valid_spot_cache(cache_path)
            if cached_rows is not None:
                return cached_rows

        rows: list[dict[str, Any]] = []
        page_size = 100
        for page in range(1, 80):
            params = {
                "pn": str(page),
                "pz": str(page_size),
                "po": "1",
                "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2",
                "invt": "2",
                "fid": "f6",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f2,f3,f5,f6,f8,f9,f12,f14,f15,f16,f17,f18,f20,f21,f23",
            }
            try:
                payload = _get_json(self.spot_url, params)
            except FreeDataError:
                fallback_rows = self._read_spot_fallback(cache_path, legacy_cache_path) if use_cache else None
                if fallback_rows is not None:
                    return fallback_rows
                raise
            page_rows = payload.get("data", {}).get("diff", [])
            if not page_rows:
                break
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                break
            time.sleep(self.request_pause)
        if len(rows) < self.min_spot_cache_rows:
            fallback_rows = self._read_spot_fallback(cache_path, legacy_cache_path) if use_cache else None
            if fallback_rows is not None:
                return fallback_rows
            raise FreeDataError(f"spot fetch incomplete: only {len(rows)} rows")
        self._write_cache(cache_path, rows)
        return rows

    def fetch_kline(
        self,
        code: str,
        start: date,
        end: date,
        adjust: str = "qfq",
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        if self.kline_source in {"mootdx", "auto"}:
            try:
                rows = self.mootdx.fetch_daily_bars(code, start, end, use_cache=use_cache)
                self.source_stats["mootdx_ok"] = self.source_stats.get("mootdx_ok", 0) + 1
                return rows
            except SourceDataError:
                self.source_stats["mootdx_failed"] = self.source_stats.get("mootdx_failed", 0) + 1

        fqt = {"none": "0", "qfq": "1", "hfq": "2"}.get(adjust, "1")
        cache_path = self.cache_dir / "kline" / adjust / f"{code}_{start:%Y%m%d}_{end:%Y%m%d}.json"
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        if use_cache:
            cached_rows = self._load_cached_kline_range(code, start, end, adjust)
            if cached_rows is not None:
                cached_last_date = _parse_date(str(cached_rows[-1]["date"]))
                if cached_last_date >= end:
                    rows = [
                        row
                        for row in cached_rows
                        if start <= _parse_date(str(row.get("date") or "")) <= end
                    ]
                    self._write_cache(cache_path, rows)
                    return rows
                tail_start = cached_last_date + timedelta(days=1)
                tail_rows = self._fetch_eastmoney_kline(code, tail_start, end, fqt)
                rows = self._merge_kline_rows(cached_rows, tail_rows)
                self._write_cache(cache_path, rows)
                return rows

        rows = self._fetch_eastmoney_kline(code, start, end, fqt)
        self._write_cache(cache_path, rows)
        return rows

    def _load_cached_kline_range(self, code: str, start: date, end: date, adjust: str) -> list[dict[str, Any]] | None:
        cache_dir = self.cache_dir / "kline" / adjust
        if not cache_dir.exists():
            return None
        candidates: list[tuple[date, float, list[dict[str, Any]]]] = []
        for path in cache_dir.glob(f"{code}_*.json"):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(rows, list) or not rows:
                continue
            first = rows[0] if isinstance(rows[0], dict) else None
            last = rows[-1] if isinstance(rows[-1], dict) else None
            if not first or not last:
                continue
            first_date = _parse_date(str(first.get("date") or ""))
            last_date = _parse_date(str(last.get("date") or ""))
            if first_date <= start and last_date >= start:
                candidates.append((last_date, _safe_mtime(path), rows))
        if not candidates:
            return None
        cached_rows = max(candidates, key=lambda item: (item[0], item[1]))[2]
        cached_rows.sort(key=lambda item: item.get("date", ""))
        return cached_rows

    def _fetch_eastmoney_kline(self, code: str, start: date, end: date, fqt: str) -> list[dict[str, Any]]:
        params = {
            "secid": _secid(code),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": fqt,
            "beg": f"{start:%Y%m%d}",
            "end": f"{end:%Y%m%d}",
        }
        try:
            payload = _get_json(self.kline_url, params)
            klines = payload.get("data", {}).get("klines") or []
            rows = [_parse_kline(line) for line in klines]
        except FreeDataError:
            rows = self._fetch_yahoo_kline(code, start, end)
        self.source_stats["eastmoney_ok"] = self.source_stats.get("eastmoney_ok", 0) + 1
        return rows

    def _merge_kline_rows(self, left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_date = {str(row.get("date")): row for row in left if isinstance(row, dict) and row.get("date")}
        by_date.update({str(row.get("date")): row for row in right if isinstance(row, dict) and row.get("date")})
        merged = [by_date[key] for key in sorted(by_date)]
        return merged

    def _fetch_yahoo_kline(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        symbol = _yahoo_symbol(code)
        params = {
            "period1": str(_epoch(start)),
            "period2": str(_epoch(end + timedelta(days=1))),
            "interval": "1d",
            "events": "history",
        }
        payload = _get_json(self.yahoo_chart_url.format(symbol=symbol), params)
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            raise FreeDataError(f"yahoo_no_result:{symbol}")
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        rows: list[dict[str, Any]] = []
        for index, timestamp in enumerate(timestamps):
            close = _list_value(quote.get("close"), index)
            if close <= 0:
                continue
            volume = _list_value(quote.get("volume"), index)
            rows.append(
                {
                    "date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(),
                    "open": _list_value(quote.get("open"), index),
                    "close": close,
                    "high": _list_value(quote.get("high"), index),
                    "low": _list_value(quote.get("low"), index),
                    "volume": volume,
                    "amount": close * volume,
                    "amplitude": 0.0,
                    "pct_change": 0.0,
                    "change": 0.0,
                    "turnover": 0.0,
                }
            )
        if not rows:
            raise FreeDataError(f"yahoo_empty:{symbol}")
        return rows

    def write_snapshots(self, snapshots: list[StockSnapshot], path: Path | str, merge: bool = False) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = [asdict(item) for item in snapshots]
        if merge and output.exists():
            existing = json.loads(output.read_text(encoding="utf-8"))
            by_code = {str(row["code"]): row for row in existing}
            by_code.update({str(row["code"]): row for row in rows})
            rows = sorted(by_code.values(), key=lambda row: str(row["code"]))
        output.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _select_candidates(self, rows: list[dict[str, Any]], limit: int, offset: int = 0) -> list[dict[str, Any]]:
        candidates = []
        for row in rows:
            code = str(row.get("f12", ""))
            name = str(row.get("f14", ""))
            if not is_allowed_code(code, self.config.universe):
                continue
            if "ST" in name.upper():
                continue
            amount = _to_float(row.get("f6"))
            price = _to_float(row.get("f2"))
            if amount < self.config.universe.min_avg_turnover or price < self.config.universe.min_price:
                continue
            candidates.append(row)
        candidates.sort(key=lambda item: _to_float(item.get("f6")), reverse=True)
        return candidates[offset : offset + limit]

    def _compute_metrics(self, row: dict[str, Any], bars: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
        if len(bars) < 61:
            return None
        bars = sorted(bars, key=lambda item: item["date"])
        last = bars[-1]
        previous = bars[-2]
        closes = [_to_float(bar["close"]) for bar in bars]
        amounts = [_to_float(bar["amount"]) for bar in bars]
        turnovers = [_to_float(bar["turnover"]) / 100 for bar in bars]
        recent_turnovers = [value for value in turnovers[-20:] if value > 0]
        turnover_rate_20d = (
            statistics.fmean(recent_turnovers)
            if recent_turnovers
            else _to_float(row.get("f8")) / 100
        )

        close = _to_float(last["close"])
        prev_close = _to_float(previous["close"])
        ma20 = statistics.fmean(closes[-20:])
        ma60 = statistics.fmean(closes[-60:])
        previous_ma60 = statistics.fmean(closes[-61:-1])
        returns = [closes[index] / closes[index - 1] - 1 for index in range(len(closes) - 20, len(closes))]
        limit_up = _price_limit(prev_close, 1.10)
        limit_down = _price_limit(prev_close, 0.90)
        last_date = _parse_date(str(last["date"]))

        return {
            "code": str(row.get("f12")),
            "name": str(row.get("f14")),
            "trade_date": last_date,
            "open_price": _to_float(last["open"]),
            "close": close,
            "prev_close": prev_close,
            "ma20": ma20,
            "ma60": ma60,
            "ma60_slope": ma60 / previous_ma60 - 1 if previous_ma60 else 0.0,
            "return_20d": close / closes[-21] - 1,
            "return_60d": close / closes[-61] - 1,
            "volatility_20d": statistics.stdev(returns) if len(returns) > 1 else 0.0,
            "avg_turnover_20d": statistics.fmean(amounts[-20:]),
            "turnover_rate_20d": turnover_rate_20d,
            "listing_days": len(bars),
            "is_st": "ST" in str(row.get("f14", "")).upper(),
            "is_suspended": False,
            "is_limit_up": close >= limit_up - 0.01,
            "is_limit_down": close <= limit_down + 0.01,
            "limit_up_price": limit_up,
            "limit_down_price": limit_down,
            "is_data_stale": last_date < day - timedelta(days=7),
            "pe": _to_float(row.get("f9")),
        }

    def _write_cache(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def _read_valid_spot_cache(self, path: Path) -> list[dict[str, Any]] | None:
        if not path.exists():
            return None
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(rows, list) and len(rows) >= self.min_spot_cache_rows:
            return rows
        return None

    def _read_spot_fallback(self, cache_path: Path, legacy_cache_path: Path) -> list[dict[str, Any]] | None:
        return (
            self._read_valid_spot_cache(cache_path)
            or self._read_valid_spot_cache(legacy_cache_path)
            or self._read_latest_valid_spot_cache(cache_path)
        )

    def _read_latest_valid_spot_cache(self, cache_path: Path) -> list[dict[str, Any]] | None:
        target_day = cache_path.stem
        candidates = sorted(cache_path.parent.glob("*.json"), key=lambda path: path.stem, reverse=True)
        for path in candidates:
            if path.stem.isdigit() and target_day.isdigit() and path.stem > target_day:
                continue
            rows = self._read_valid_spot_cache(path)
            if rows is not None:
                return rows
        return None


class FreeDataError(RuntimeError):
    pass


def _get_json(url: str, params: dict[str, str], retries: int = 2, timeout_seconds: int = 8) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            last_error = error
            time.sleep(0.5 * (attempt + 1))
    raise FreeDataError(f"failed_to_fetch:{url}:{last_error}")


def _parse_kline(line: str) -> dict[str, Any]:
    fields = line.split(",")
    return {
        "date": fields[0],
        "open": _to_float(fields[1]),
        "close": _to_float(fields[2]),
        "high": _to_float(fields[3]),
        "low": _to_float(fields[4]),
        "volume": _to_float(fields[5]),
        "amount": _to_float(fields[6]),
        "amplitude": _to_float(fields[7]),
        "pct_change": _to_float(fields[8]),
        "change": _to_float(fields[9]),
        "turnover": _to_float(fields[10]),
    }


def _secid(code: str) -> str:
    return f"1.{code}" if code.startswith(("600", "601", "603", "605", "688", "689")) else f"0.{code}"


def _yahoo_symbol(code: str) -> str:
    return f"{code}.SS" if code.startswith(("600", "601", "603", "605", "688", "689")) else f"{code}.SZ"


def _epoch(day: date) -> int:
    return int(datetime.combine(day, datetime_time.min, tzinfo=timezone.utc).timestamp())


def _to_float(value: Any) -> float:
    if value in (None, "-", ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number


def _list_value(values: list[Any] | None, index: int) -> float:
    if not values or index >= len(values):
        return 0.0
    return _to_float(values[index])


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _price_limit(prev_close: float, multiplier: float) -> float:
    return round(prev_close * multiplier + 1e-8, 2)


def _pe_ranks(metrics: dict[str, dict[str, Any]]) -> dict[str, float]:
    positive_pe = {code: item["pe"] for code, item in metrics.items() if item.get("pe", 0) > 0}
    ranks = rank01(positive_pe, reverse=False)
    return {code: ranks.get(code, 0.5) for code in metrics}
