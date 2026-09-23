from __future__ import annotations

import importlib.util
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any


class SourceDataError(RuntimeError):
    pass


class MootdxKlineClient:
    """Optional mootdx daily K-line adapter.

    mootdx is intentionally imported lazily so the rest of the system keeps
    running when the package is not installed or the TDX servers are busy.
    """

    def __init__(self, cache_dir: Path | str = Path("data/cache/source/mootdx")) -> None:
        self.cache_dir = Path(cache_dir)

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("mootdx") is not None

    def fetch_daily_bars(
        self,
        code: str,
        start: date,
        end: date,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        cache_path = self.cache_dir / f"{code}_{start:%Y%m%d}_{end:%Y%m%d}.json"
        if use_cache and cache_path.exists():
            return _filter_valid_bar_rows(json.loads(cache_path.read_text(encoding="utf-8")), start, end)
        if not self.available():
            raise SourceDataError("mootdx_not_installed")

        self._prepare_home()
        try:
            from mootdx.quotes import Quotes
            from mootdx import config as mootdx_config
        except Exception as error:  # pragma: no cover - depends on optional package
            raise SourceDataError(f"mootdx_import_failed:{error}") from error

        offset = max(120, (end - start).days + 40)
        last_error: Exception | None = None
        for server in _mootdx_servers(mootdx_config):
            try:
                client = Quotes.factory(market="std", bestip=False, server=server, timeout=6)
                try:
                    frame = client.bars(symbol=code, frequency=9, offset=offset)
                except TypeError:
                    frame = client.bars(symbol=code, frequency=9)
                finally:
                    if hasattr(client, "close"):
                        client.close()
                rows = _normalise_dataframe_bars(frame, start, end)
                if rows:
                    _write_json(cache_path, rows)
                    return rows
            except Exception as error:  # pragma: no cover - network/server dependent
                last_error = error
                continue
        raise SourceDataError(f"mootdx_fetch_failed:{code}:{last_error}")

    def _prepare_home(self) -> None:
        home = self.cache_dir / "home"
        home.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(home.resolve())


class TencentQuoteClient:
    quote_url = "https://qt.gtimg.cn/q={symbols}"
    default_index_symbols = ("sh000001", "sz399001", "sz399006", "sh000300", "sh000905")

    def __init__(self, cache_dir: Path | str = Path("data/cache/source/tencent"), pause: float = 0.15) -> None:
        self.cache_dir = Path(cache_dir)
        self.pause = pause

    def fetch_quotes(self, codes: list[str], trade_date: date | None = None, use_cache: bool = True) -> dict[str, dict[str, Any]]:
        day = trade_date or date.today()
        cache_path = self.cache_dir / "quotes" / f"{day:%Y%m%d}.json"
        quotes: dict[str, dict[str, Any]] = {}
        if use_cache and cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            quotes.update({code: cached[code] for code in codes if code in cached})
            missing_codes = [code for code in codes if code not in quotes]
            if not missing_codes:
                return quotes
        else:
            missing_codes = list(codes)

        symbols = [_tencent_symbol(code) for code in missing_codes]
        for index in range(0, len(symbols), 80):
            chunk = symbols[index : index + 80]
            text = _get_text(self.quote_url.format(symbols=",".join(chunk)), referer="https://gu.qq.com/")
            quotes.update(_parse_tencent_quotes(text))
            time.sleep(self.pause)
        _write_json(cache_path, quotes)
        return quotes

    def fetch_market_indices(
        self,
        symbols: list[str] | tuple[str, ...] | None = None,
        trade_date: date | None = None,
        use_cache: bool = True,
    ) -> dict[str, dict[str, Any]]:
        day = trade_date or date.today()
        wanted = list(symbols or self.default_index_symbols)
        cache_path = self.cache_dir / "index_quotes" / f"{day:%Y%m%d}.json"
        quotes: dict[str, dict[str, Any]] = {}
        if use_cache and cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            quotes.update({symbol: cached[symbol] for symbol in wanted if symbol in cached})
            missing_symbols = [symbol for symbol in wanted if symbol not in quotes]
            if not missing_symbols:
                return quotes
        else:
            missing_symbols = wanted

        if missing_symbols:
            text = _get_text(self.quote_url.format(symbols=",".join(missing_symbols)), referer="https://gu.qq.com/")
            parsed = _parse_tencent_quotes(text)
            by_symbol = {f"{quote.get('market', '').lower()}{quote.get('code', '')}": quote for quote in parsed.values()}
            for symbol in missing_symbols:
                quote = by_symbol.get(symbol)
                if not quote:
                    continue
                quotes[symbol] = {
                    **quote,
                    "symbol": symbol,
                    "label": _index_label(symbol, quote),
                    "source_note": "腾讯财经指数行情；用于判断当日大盘环境，不作为单一买卖依据。",
                }
            time.sleep(self.pause)
        _write_json(cache_path, quotes)
        return quotes


class TonghuashunHotClient:
    stock_urls = (
        "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal",
        "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=day&list_type=normal",
    )
    concept_urls: tuple[str, ...] = ()

    def __init__(self, cache_dir: Path | str = Path("data/cache/source/tonghuashun"), pause: float = 0.3) -> None:
        self.cache_dir = Path(cache_dir)
        self.pause = pause
        self.stats: dict[str, Any] = {}

    def collect_theme_evidence(
        self,
        codes: list[str],
        trade_date: date | None = None,
        use_cache: bool = True,
    ) -> dict[str, dict[str, Any]]:
        day = trade_date or date.today()
        cache_path = self.cache_dir / f"hot_{day:%Y%m%d}.json"
        if use_cache and cache_path.exists():
            payloads = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            payloads = []
            errors: list[str] = []
            for url in (*self.stock_urls, *self.concept_urls):
                try:
                    payloads.append({"url": url, "payload": _get_json_url(url, referer="https://www.10jqka.com.cn/")})
                except SourceDataError as error:
                    errors.append(str(error))
                time.sleep(self.pause)
            _write_json(cache_path, payloads)
            self.stats["errors"] = errors

        wanted = set(codes)
        evidence: dict[str, dict[str, Any]] = {}
        records_seen = 0
        for item in payloads:
            source_url = str(item.get("url", "tonghuashun_hot"))
            for record in _walk_dicts(item.get("payload")):
                code = _extract_code(record)
                if not code or code not in wanted:
                    continue
                records_seen += 1
                theme_item = _theme_item_from_record(record, source_url)
                target = evidence.setdefault(code, {"source": "tonghuashun_hot", "items": []})
                target["items"].append(theme_item)
                if target.get("hot_rank") is None or theme_item.get("hot_rank", 10_000) < target["hot_rank"]:
                    target["hot_rank"] = theme_item.get("hot_rank")
                target["hot_score"] = max(float(target.get("hot_score") or 0.0), float(theme_item.get("hot_score") or 0.0))
                themes = set(target.get("themes") or [])
                themes.update(theme_item.get("themes") or [])
                target["themes"] = sorted(themes)

        for item in evidence.values():
            rank = item.get("hot_rank")
            score = float(item.get("hot_score") or 0.0)
            item["is_hot"] = bool(rank and rank <= 100) or score > 0
            item["summary"] = _theme_summary(item)
        self.stats = {
            **self.stats,
            "source": "tonghuashun_public_hot",
            "codes": len(codes),
            "matched_codes": len(evidence),
            "records_seen": records_seen,
        }
        return evidence


def _normalise_dataframe_bars(frame: Any, start: date, end: date) -> list[dict[str, Any]]:
    if hasattr(frame, "reset_index"):
        try:
            frame = frame.reset_index()
        except ValueError:
            pass
    if not hasattr(frame, "to_dict"):
        return []
    records = frame.to_dict("records")
    rows: list[dict[str, Any]] = []
    for record in records:
        row_date = _record_date(record)
        if row_date is None or row_date < start or row_date > end:
            continue
        close = _to_float(_first_value(record, ("close", "收盘", "price")))
        if close <= 0:
            continue
        volume = _to_float(_first_value(record, ("volume", "vol", "成交量")))
        amount = _to_float(_first_value(record, ("amount", "成交额")))
        if volume < 1:
            continue
        if amount < 1:
            amount = close * volume
        if amount < 1:
            continue
        rows.append(
            {
                "date": row_date.isoformat(),
                "open": _to_float(_first_value(record, ("open", "开盘"))),
                "close": close,
                "high": _to_float(_first_value(record, ("high", "最高"))),
                "low": _to_float(_first_value(record, ("low", "最低"))),
                "volume": volume,
                "amount": amount,
                "amplitude": 0.0,
                "pct_change": 0.0,
                "change": 0.0,
                "turnover": 0.0,
            }
        )
    return sorted(rows, key=lambda item: item["date"])


def _filter_valid_bar_rows(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for row in rows:
        row_date = _record_date(row)
        if row_date is None or row_date < start or row_date > end:
            continue
        if _to_float(row.get("close")) <= 0:
            continue
        if _to_float(row.get("volume")) < 1:
            continue
        if _to_float(row.get("amount")) < 1:
            continue
        item = dict(row)
        item["date"] = row_date.isoformat()
        valid.append(item)
    return sorted(valid, key=lambda item: item["date"])


def _mootdx_servers(config: Any) -> list[tuple[str, int]]:
    servers: list[tuple[str, int]] = []
    best = ((config.get("BESTIP") or {}).get("HQ") if hasattr(config, "get") else None) or []
    if len(best) >= 2:
        servers.append((str(best[0]), int(best[1])))
    raw_servers = ((config.get("SERVER") or {}).get("HQ") if hasattr(config, "get") else None) or []
    for item in raw_servers:
        if len(item) < 3:
            continue
        server = (str(item[1]), int(item[2]))
        if server not in servers:
            servers.append(server)
        if len(servers) >= 12:
            break
    return servers


def _parse_tencent_quotes(text: str) -> dict[str, dict[str, Any]]:
    quotes: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r"v_([a-z]{2})(\d{6})=\"([^\"]*)\"", text):
        market, code, payload = match.groups()
        fields = payload.split("~")
        if len(fields) < 39:
            continue
        trade_triplet = _field(fields, 35).split("/")
        amount_yuan = _to_float(trade_triplet[2]) if len(trade_triplet) >= 3 else _to_float(_field(fields, 57)) * 10_000
        quotes[code] = {
            "source": "tencent:qt.gtimg.cn/full_quote",
            "market": market.upper(),
            "name": _field(fields, 1),
            "code": code,
            "price": _to_float(_field(fields, 3)),
            "prev_close": _to_float(_field(fields, 4)),
            "open": _to_float(_field(fields, 5)),
            "quote_time": _field(fields, 30),
            "change": _to_float(_field(fields, 31)),
            "price_change": _to_float(_field(fields, 31)),
            "pct_change": _to_float(_field(fields, 32)),
            "high": _to_float(_field(fields, 33)),
            "low": _to_float(_field(fields, 34)),
            "volume_lot": _to_float(_field(fields, 36)),
            "amount_10k": _to_float(_field(fields, 37)),
            "amount_yuan": amount_yuan,
            "turnover_rate_pct": _to_float(_field(fields, 38)),
            "turnover_rate": _to_float(_field(fields, 38)) / 100,
            "pe_dynamic": _to_float(_field(fields, 39)),
            "amplitude_pct": _to_float(_field(fields, 43)),
            "float_market_cap_100m": _to_float(_field(fields, 44)),
            "total_market_cap_100m": _to_float(_field(fields, 45)),
            "pb": _to_float(_field(fields, 46)),
            "limit_up": _to_float(_field(fields, 47)),
            "limit_down": _to_float(_field(fields, 48)),
            "volume_ratio": _to_float(_field(fields, 49)),
            "avg_price": _to_float(_field(fields, 51)),
            "source_note": "腾讯财经长行情字段；用于盘口、PE、换手率、市值等辅助信息，不作为单一买入依据。",
            "raw_fields": fields[:88],
        }
    return quotes


def _theme_item_from_record(record: dict[str, Any], source_url: str) -> dict[str, Any]:
    rank = _to_float(_first_value(record, ("rank", "hot_rank", "order", "ranking", "heat_rank")))
    hot_score = _to_float(_first_value(record, ("hot", "heat", "score", "hot_score", "rate")))
    themes = _extract_themes(record)
    return {
        "source_url": source_url,
        "hot_rank": int(rank) if rank > 0 else None,
        "hot_score": hot_score,
        "name": str(_first_value(record, ("name", "stock_name", "short_name", "股票简称")) or ""),
        "reason": str(_first_value(record, ("reason", "analyse", "analysis", "brief", "涨停原因", "入选理由")) or ""),
        "themes": themes,
    }


def _extract_themes(record: dict[str, Any]) -> list[str]:
    themes: set[str] = set()
    for key, value in record.items():
        key_text = str(key).lower()
        if not any(token in key_text for token in ("concept", "plate", "theme", "tag", "题材", "概念", "板块")):
            continue
        if isinstance(value, str):
            for part in re.split(r"[,，/、| ]+", value):
                if 1 < len(part) <= 16:
                    themes.add(part)
        elif isinstance(value, dict):
            for item in _walk_dicts(value):
                for name in item.values():
                    if isinstance(name, str) and 1 < len(name) <= 16:
                        themes.add(name)
                    elif isinstance(name, list):
                        for child in name:
                            if child:
                                themes.add(str(child))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = _first_value(item, ("name", "plate_name", "concept_name", "title"))
                    if name:
                        themes.add(str(name))
                elif item:
                    themes.add(str(item))
    return sorted(themes)


def _theme_summary(item: dict[str, Any]) -> str:
    rank = item.get("hot_rank")
    themes = "、".join(item.get("themes") or [])
    if rank and themes:
        return f"同花顺热度排名{rank}，题材：{themes}"
    if rank:
        return f"同花顺热度排名{rank}"
    if themes:
        return f"同花顺题材：{themes}"
    return "同花顺热点命中"


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_dicts(child))
    return found


def _extract_code(record: dict[str, Any]) -> str | None:
    for key in ("code", "stock_code", "stockCode", "symbol", "market_code", "stockcode", "security_code"):
        value = record.get(key)
        if value is None:
            continue
        match = re.search(r"(\d{6})", str(value))
        if match:
            return match.group(1)
    for value in record.values():
        if isinstance(value, str):
            match = re.search(r"(?<!\d)([036]\d{5}|00\d{4}|001\d{3}|002\d{3}|003\d{3})(?!\d)", value)
            if match:
                return match.group(1)
    return None


def _get_json_url(url: str, referer: str) -> Any:
    text = _get_text(url, referer=referer)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise SourceDataError(f"invalid_json:{url}:{error}") from error


def _get_text(url: str, referer: str, retries: int = 2) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": referer,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="ignore")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            last_error = error
            time.sleep(0.4 * (attempt + 1))
    raise SourceDataError(f"failed_to_fetch:{url}:{last_error}")


def _record_date(record: dict[str, Any]) -> date | None:
    value = _first_value(record, ("date", "datetime", "time", "index"))
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _first_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    lower = {str(key).lower(): value for key, value in record.items()}
    for key in keys:
        if key.lower() in lower:
            return lower[key.lower()]
    return None


def _field(fields: list[str], index: int) -> str:
    return fields[index] if index < len(fields) else ""


def _tencent_symbol(code: str) -> str:
    prefix = "sh" if code.startswith(("600", "601", "603", "605", "688", "689")) else "sz"
    return f"{prefix}{code}"


def _index_label(symbol: str, quote: dict[str, Any]) -> str:
    return {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz399006": "创业板指",
        "sh000300": "沪深300",
        "sh000905": "中证500",
    }.get(symbol, str(quote.get("name") or symbol))


def _to_float(value: Any) -> float:
    try:
        if value in (None, "", "-"):
            return 0.0
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
