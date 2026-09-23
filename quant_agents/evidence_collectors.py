from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


class EvidenceCollectionError(RuntimeError):
    pass


RISKY_HOT_MONEY_SEATS = (
    "拉萨",
    "紫阳东路",
    "知春路",
)


class EastmoneyEvidenceCollector:
    data_center_url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    margin_chart_url = "https://datapc.eastmoney.com/emdatacenter/rzrq/detailchart2"

    def __init__(self, cache_dir: Path | str = Path("data/cache/evidence/eastmoney"), pause: float = 0.25) -> None:
        self.cache_dir = Path(cache_dir)
        self.pause = pause
        self.stats: dict[str, object] = {}

    def collect(
        self,
        trade_date: date,
        codes: list[str],
        max_margin_symbols: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        evidence: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        try:
            dragon_tiger = self.collect_dragon_tiger(trade_date)
            _merge(evidence, dragon_tiger)
        except EvidenceCollectionError as error:
            errors.append(str(error))

        margin_codes = codes if max_margin_symbols is None else codes[: max(0, max_margin_symbols)]
        margin_ok = 0
        margin_failed = 0
        for code in margin_codes:
            try:
                margin_item = self.collect_margin_for_code(code)
            except EvidenceCollectionError:
                margin_failed += 1
                continue
            if margin_item:
                evidence.setdefault(code, {}).setdefault("margin", {}).update(margin_item)
                margin_ok += 1
            time.sleep(self.pause)

        self.stats = {
            "source": "eastmoney_public",
            "trade_date": f"{trade_date:%Y-%m-%d}",
            "codes": len(codes),
            "margin_attempted": len(margin_codes),
            "margin_ok": margin_ok,
            "margin_failed": margin_failed,
            "evidence_codes": len(evidence),
            "errors": errors,
        }
        return evidence

    def collect_dragon_tiger(self, trade_date: date) -> dict[str, dict[str, Any]]:
        cache_path = self.cache_dir / "dragon_tiger" / f"{trade_date:%Y%m%d}.json"
        rows = _read_json(cache_path)
        if rows is None:
            rows = []
            page = 1
            while True:
                payload = self._data_center(
                    report_name="RPT_DAILYBILLBOARD_DETAILS",
                    page=page,
                    page_size=200,
                    sort_columns="TRADE_DATE,SECURITY_CODE",
                    sort_types="-1,1",
                    filter_text=f"(TRADE_DATE='{trade_date:%Y-%m-%d}')",
                )
                page_rows = payload.get("result", {}).get("data") or []
                if not page_rows:
                    break
                rows.extend(page_rows)
                total = int(payload.get("result", {}).get("count") or len(rows))
                if len(rows) >= total:
                    break
                page += 1
                time.sleep(self.pause)
            _write_json(cache_path, rows)

        evidence: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row.get("SECURITY_CODE", ""))
            if not code:
                continue
            explain = str(row.get("EXPLAIN") or row.get("EXPLANATION") or "")
            institution_count = _institution_count(explain)
            net_amount = _to_float(row.get("BILLBOARD_NET_AMT"))
            institution_net_buy = 0.0
            if institution_count:
                institution_net_buy = net_amount
            item = {
                "dragon_tiger": {
                    "institution_net_buy": institution_net_buy,
                    "institution_seat_count": institution_count,
                    "billboard_net_buy": net_amount,
                    "billboard_buy_amount": _to_float(row.get("BILLBOARD_BUY_AMT")),
                    "billboard_sell_amount": _to_float(row.get("BILLBOARD_SELL_AMT")),
                    "buy_seat_names": seat_names_from_row(row, "buy"),
                    "sell_seat_names": seat_names_from_row(row, "sell"),
                    "seat_quality": assess_dragon_tiger_seat_quality(row),
                    "explain": explain,
                    "trade_date": str(row.get("TRADE_DATE", ""))[:10],
                    "source": "eastmoney:RPT_DAILYBILLBOARD_DETAILS",
                    "source_note": "龙虎榜公开信息；机构净买入为根据公开字段和说明的估算，不代表具体机构实时持仓。",
                }
            }
            evidence.setdefault(code, {}).update(item)
        return evidence

    def collect_margin_for_code(self, code: str) -> dict[str, Any] | None:
        cache_path = self.cache_dir / "margin" / f"{code}.json"
        payload = _read_json(cache_path)
        if payload is None:
            params = {"code": _market_code(code)}
            payload = _get_json(self.margin_chart_url, params, referer="https://datapc.eastmoney.com/")
            _write_json(cache_path, payload)
        result = payload.get("result") or {}
        rows = result.get("data") or []
        if len(rows) < 2:
            return None
        rows = sorted(rows, key=lambda row: str(row.get("DATE", "")))
        latest = rows[-1]
        previous = rows[-2]
        latest_balance = _to_float(latest.get("RZRQYECZ"))
        previous_balance = _to_float(previous.get("RZRQYECZ"))
        return {
            "financing_balance_change": latest_balance - previous_balance,
            "latest_margin_balance": latest_balance,
            "previous_margin_balance": previous_balance,
            "latest_date": str(latest.get("DATE", ""))[:10],
            "source": "eastmoney:rzrq/detailchart2",
            "source_note": "融资融券公开页面余额序列；作为杠杆资金活跃线索，不等同机构买入。",
        }

    def _data_center(
        self,
        report_name: str,
        page: int,
        page_size: int,
        sort_columns: str,
        sort_types: str,
        filter_text: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "sortColumns": sort_columns,
            "sortTypes": sort_types,
            "pageSize": str(page_size),
            "pageNumber": str(page),
            "reportName": report_name,
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
        }
        if filter_text:
            params["filter"] = filter_text
        payload = _get_json(self.data_center_url, params, referer="https://data.eastmoney.com/")
        if not payload.get("success"):
            raise EvidenceCollectionError(f"data_center_failed:{report_name}:{payload.get('message')}")
        return payload


def write_evidence(path: Path | str, evidence: dict[str, dict[str, Any]], merge: bool = False) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if merge and output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        for code, item in evidence.items():
            existing.setdefault(code, {})
            _deep_update(existing[code], item)
        evidence = existing
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _merge(target: dict[str, dict[str, Any]], source: dict[str, dict[str, Any]]) -> None:
    for code, item in source.items():
        target.setdefault(code, {})
        _deep_update(target[code], item)


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _get_json(url: str, params: dict[str, str], referer: str) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": referer},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _market_code(code: str) -> str:
    suffix = "SH" if code.startswith(("600", "601", "603", "605", "688", "689")) else "SZ"
    return f"{code}.{suffix}"


def assess_dragon_tiger_seat_quality(row: dict[str, Any]) -> dict[str, Any]:
    buy_names = seat_names_from_row(row, "buy")
    sell_names = seat_names_from_row(row, "sell")
    buy_risk = risky_seat_hits(buy_names)
    sell_risk = risky_seat_hits(sell_names)
    score_adjustment = round(max(-0.12, -0.04 * len(buy_risk) - 0.01 * len(sell_risk)), 4)
    if buy_risk:
        label = "risky_hot_money_buy"
    elif sell_risk:
        label = "risky_hot_money_sell"
    else:
        label = "neutral"
    return {
        "label": label,
        "score_adjustment": score_adjustment,
        "risk_seats": buy_risk + sell_risk,
        "buy_risk_seats": buy_risk,
        "sell_risk_seats": sell_risk,
        "note": "风险游资席位仅做小幅降权，不做一票否决。",
    }


def seat_names_from_row(row: dict[str, Any], side: str) -> list[str]:
    prefixes = ("BUY", "BUYER") if side == "buy" else ("SELL", "SELLER")
    values: list[str] = []
    explicit_key = f"{side}_seat_names"
    explicit = row.get(explicit_key)
    if isinstance(explicit, list):
        values.extend(str(item) for item in explicit if item)
    for key, value in row.items():
        key_text = str(key).upper()
        if not any(key_text.startswith(prefix) for prefix in prefixes):
            continue
        if not any(token in key_text for token in ("NAME", "SEAT", "营业部", "席位")):
            continue
        if isinstance(value, list):
            values.extend(str(item) for item in value if item)
        elif value:
            values.append(str(value))
    return list(dict.fromkeys(values))


def risky_seat_hits(names: list[str]) -> list[str]:
    hits: list[str] = []
    for name in names:
        if any(keyword in name for keyword in RISKY_HOT_MONEY_SEATS):
            hits.append(name)
    return hits


def _institution_count(text: str) -> int:
    total = 0
    for match in re.findall(r"(\d+)家机构", text):
        total += int(match)
    return total


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
