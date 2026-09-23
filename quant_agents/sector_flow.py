from __future__ import annotations

from copy import deepcopy
from statistics import mean
from typing import Any


NON_SECTOR_TOKENS = (
    "板",
    "涨停",
    "跌停",
    "昨日",
    "今日",
    "高标",
    "炸板",
    "强势股",
    "年报",
    "一季报",
)
REAL_FLOW_KEYS = (
    "main_net_inflow_yuan",
    "net_main_inflow_yuan",
    "main_net_flow_yuan",
    "main_net_inflow",
)


def build_global_sector_flow(
    context_evidence: dict[str, dict[str, Any]],
    previous: dict[str, Any] | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    rows_by_sector: dict[str, list[dict[str, Any]]] = {}
    latest_quote_time: str | None = None
    for code, item in context_evidence.items():
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        theme = item.get("theme") if isinstance(item.get("theme"), dict) else {}
        sectors = sector_theme_names(theme)
        pct_change = number(quote.get("pct_change"))
        amount = positive(quote.get("amount_yuan"))
        if not sectors or pct_change is None or amount is None:
            continue
        quote_time = str(quote.get("quote_time") or "")
        if quote_time and (latest_quote_time is None or quote_time > latest_quote_time):
            latest_quote_time = quote_time
        real_flow = first_number(*(quote.get(key) for key in REAL_FLOW_KEYS))
        volume_ratio = positive(quote.get("volume_ratio")) or 1.0
        inferred_flow = amount * clamp(pct_change / 5.0, -1.0, 1.0) * clamp(volume_ratio, 0.6, 2.5) * 0.35
        row = {
            "code": str(code),
            "name": quote.get("name") or code,
            "pctChange": pct_change,
            "amountYuan": amount,
            "volumeRatio": volume_ratio,
            "hotRank": positive(theme.get("hot_rank")),
            "hotScore": positive(theme.get("hot_score")),
            "netFlowYuan": real_flow if real_flow is not None else inferred_flow,
            "flowSource": "reported" if real_flow is not None else "inferred",
            "isLimitUp": pct_change >= 9.8,
            "isLimitDown": pct_change <= -9.8,
        }
        for sector in sectors:
            rows_by_sector.setdefault(sector, []).append(row)

    previous_by_name = {
        str(row.get("name")): row
        for row in ((previous or {}).get("sectors") or [])
        if isinstance(row, dict) and row.get("name")
    }
    sectors: list[dict[str, Any]] = []
    for sector_name, rows in rows_by_sector.items():
        total_amount = sum(float(row["amountYuan"]) for row in rows)
        net_flow = sum(float(row["netFlowYuan"]) for row in rows)
        net_flow_ratio = net_flow / total_amount * 100 if total_amount else 0.0
        avg_pct = mean(float(row["pctChange"]) for row in rows)
        weighted_pct = sum(float(row["pctChange"]) * float(row["amountYuan"]) for row in rows) / total_amount
        up_count = sum(1 for row in rows if float(row["pctChange"]) > 0)
        down_count = sum(1 for row in rows if float(row["pctChange"]) < 0)
        up_ratio = up_count / len(rows)
        down_ratio = down_count / len(rows)
        avg_volume_ratio = mean(float(row["volumeRatio"]) for row in rows)
        reported_count = sum(1 for row in rows if row["flowSource"] == "reported")
        best_hot_rank = min((float(row["hotRank"]) for row in rows if row.get("hotRank")), default=None)
        concentration = max(float(row["amountYuan"]) for row in rows) / total_amount if total_amount else 0.0

        flow_component = clamp((net_flow_ratio + 3.0) / 8.0)
        momentum_component = clamp((weighted_pct + 2.0) / 7.0)
        breadth_component = clamp(up_ratio)
        activity_component = clamp(avg_volume_ratio / 2.0)
        heat_component = clamp((101.0 - best_hot_rank) / 100.0) if best_hot_rank else 0.35
        recognition = 100 * (
            0.32 * flow_component
            + 0.20 * momentum_component
            + 0.18 * breadth_component
            + 0.15 * activity_component
            + 0.15 * heat_component
        )
        if concentration >= 0.75:
            recognition -= 12
        elif concentration >= 0.60:
            recognition -= 6
        recognition = round(clamp(recognition, 0.0, 100.0), 1)

        previous_row = previous_by_name.get(sector_name) or {}
        previous_flow_ratio = number(previous_row.get("netFlowRatio"))
        flow_delta = None if previous_flow_ratio is None else round(net_flow_ratio - previous_flow_ratio, 2)
        status = classify_flow_status(
            recognition,
            net_flow_ratio,
            weighted_pct,
            up_ratio,
            down_ratio,
            avg_volume_ratio,
            concentration,
            flow_delta,
        )

        ranked_stocks = sorted(rows, key=lambda row: stock_leadership_score(row, total_amount), reverse=True)
        leader = stock_summary(ranked_stocks[0], "leader") if ranked_stocks else None
        hot_stocks = [
            stock_summary(row, "leader" if index == 0 else "hot")
            for index, row in enumerate(ranked_stocks[:3])
        ]
        sectors.append(
            {
                "name": sector_name,
                "recognitionScore": recognition,
                "status": status,
                "netFlowYuan": round(net_flow, 2),
                "netFlowRatio": round(net_flow_ratio, 2),
                "flowDelta": flow_delta,
                "avgPctChange": round(avg_pct, 2),
                "amountWeightedPctChange": round(weighted_pct, 2),
                "amountYuan": round(total_amount, 2),
                "avgVolumeRatio": round(avg_volume_ratio, 2),
                "upRatio": round(up_ratio, 4),
                "downRatio": round(down_ratio, 4),
                "stockCount": len(rows),
                "limitUpCount": sum(1 for row in rows if row["isLimitUp"]),
                "limitDownCount": sum(1 for row in rows if row["isLimitDown"]),
                "concentration": round(concentration, 4),
                "flowSource": "reported" if reported_count == len(rows) else "mixed" if reported_count else "inferred",
                "leader": leader,
                "hotStocks": hot_stocks,
            }
        )

    sectors.sort(key=lambda row: (float(row["recognitionScore"]), float(row["netFlowRatio"])), reverse=True)
    monitoring_active = quote_clock(latest_quote_time) >= "09:50" if latest_quote_time else False
    hot_sectors = [
        row
        for row in sectors
        if monitoring_active and row["recognitionScore"] >= 55 and row["status"] not in {"cooling", "outflow"}
    ][:top_n]
    hot_names = {row["name"] for row in hot_sectors}
    stock_signals: dict[str, dict[str, Any]] = {}
    for sector in sectors:
        for index, stock in enumerate(sector["hotStocks"]):
            code = str(stock["code"])
            candidate = stock_flow_signal(sector, "leader" if index == 0 else "hot", sector["name"] in hot_names)
            old = stock_signals.get(code)
            if old is None or float(candidate["recognitionScore"]) > float(old["recognitionScore"]):
                stock_signals[code] = candidate
        for row in rows_by_sector.get(sector["name"], []):
            code = str(row["code"])
            candidate = stock_flow_signal(sector, "member", sector["name"] in hot_names)
            old = stock_signals.get(code)
            if old is None or float(candidate["recognitionScore"]) > float(old["recognitionScore"]):
                stock_signals[code] = candidate

    return {
        "identifiedAt": latest_quote_time,
        "monitoringActive": monitoring_active,
        "method": "reported_main_flow_when_available_else_amount_direction_proxy",
        "hotSectorCount": len(hot_sectors),
        "hotSectors": hot_sectors,
        "sectors": sectors,
        "stockSignals": stock_signals,
    }


def apply_global_sector_flow(
    context_evidence: dict[str, dict[str, Any]],
    global_flow: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    enriched = deepcopy(context_evidence)
    signals = global_flow.get("stockSignals") if isinstance(global_flow.get("stockSignals"), dict) else {}
    for code, signal in signals.items():
        if code not in enriched:
            continue
        theme = enriched[code].get("theme") if isinstance(enriched[code].get("theme"), dict) else {}
        theme["globalSectorFlow"] = deepcopy(signal)
        enriched[code]["theme"] = theme
    return enriched


def sector_theme_names(theme: dict[str, Any]) -> list[str]:
    values: list[str] = []
    raw = theme.get("themes")
    if isinstance(raw, list):
        values.extend(str(item) for item in raw if item)
    for item in theme.get("items") or []:
        if not isinstance(item, dict):
            continue
        nested = item.get("themes")
        if isinstance(nested, list):
            values.extend(str(value) for value in nested if value)
    unique = list(dict.fromkeys(values))
    filtered = [name for name in unique if not any(token in name for token in NON_SECTOR_TOKENS)]
    return filtered or unique


def classify_flow_status(
    recognition: float,
    net_flow_ratio: float,
    weighted_pct: float,
    up_ratio: float,
    down_ratio: float,
    avg_volume_ratio: float,
    concentration: float,
    flow_delta: float | None,
) -> str:
    if net_flow_ratio <= -1.0 or (weighted_pct <= -2.0 and down_ratio >= 0.65):
        return "outflow"
    if net_flow_ratio < 0 or (weighted_pct < 0 and down_ratio >= 0.55):
        return "cooling"
    if weighted_pct >= 6.0 or concentration >= 0.75:
        return "overheated"
    if recognition >= 72 and net_flow_ratio >= 1.0 and avg_volume_ratio >= 1.35 and (flow_delta is None or flow_delta >= 0):
        return "accelerating"
    if recognition >= 60 and net_flow_ratio > 0 and up_ratio >= 0.55:
        return "sustained"
    if up_ratio > 0.35 and down_ratio > 0.35:
        return "divergent"
    return "neutral"


def stock_flow_signal(sector: dict[str, Any], role: str, is_hot_sector: bool) -> dict[str, Any]:
    status = str(sector.get("status") or "neutral")
    recognition = float(sector.get("recognitionScore") or 0)
    bonus = 0.0
    if is_hot_sector and status in {"accelerating", "sustained", "overheated"}:
        bonus = 0.012
        if role == "leader":
            bonus += 0.023
        elif role == "hot":
            bonus += 0.012
        if status == "overheated":
            bonus *= 0.5
    return {
        "primarySector": sector.get("name"),
        "role": role,
        "isHotSector": is_hot_sector,
        "scoreBonus": round(min(bonus, 0.04), 4),
        "recognitionScore": recognition,
        "status": status,
        "netFlowRatio": sector.get("netFlowRatio"),
        "flowDelta": sector.get("flowDelta"),
        "flowSource": sector.get("flowSource"),
    }


def stock_leadership_score(row: dict[str, Any], total_amount: float) -> float:
    amount_share = float(row["amountYuan"]) / total_amount if total_amount else 0.0
    pct_component = clamp((float(row["pctChange"]) + 2.0) / 12.0)
    volume_component = clamp(float(row["volumeRatio"]) / 3.0)
    hot_rank = positive(row.get("hotRank"))
    heat_component = clamp((101.0 - hot_rank) / 100.0) if hot_rank else 0.25
    return 0.35 * pct_component + 0.30 * amount_share + 0.20 * volume_component + 0.15 * heat_component


def stock_summary(row: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "code": row.get("code"),
        "name": row.get("name"),
        "role": role,
        "pctChange": round(float(row.get("pctChange") or 0), 2),
        "amountYuan": round(float(row.get("amountYuan") or 0), 2),
        "volumeRatio": round(float(row.get("volumeRatio") or 0), 2),
        "hotRank": int(row["hotRank"]) if row.get("hotRank") else None,
    }


def quote_clock(value: str | None) -> str:
    text = str(value or "")
    return f"{text[8:10]}:{text[10:12]}" if len(text) >= 12 else ""


def first_number(*values: Any) -> float | None:
    for value in values:
        result = number(value)
        if result is not None:
            return result
    return None


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def positive(value: Any) -> float | None:
    result = number(value)
    return result if result is not None and result > 0 else None


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))
