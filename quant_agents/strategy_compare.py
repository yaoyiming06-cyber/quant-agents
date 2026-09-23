from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from .utils import clamp


TARGET_RETURN_PERIODS = (1, 3, 5, 10, 20)


STRATEGY_A = {
    "id": "A_stable",
    "name": "主策略 A",
    "description": "当前稳定优选池策略；继续作为模拟交易主线。",
}

STRATEGY_B = {
    "id": "B_sector_enhanced",
    "name": "影子策略 B",
    "description": "板块资金流增强版；只记录候选与后续表现，不影响模拟交易。",
    "weights": {
        "trend": 0.30,
        "capital": 0.28,
        "liquidity": 0.06,
        "sentiment": 0.14,
        "institutional": 0.08,
        "information": 0.08,
        "fundamental": 0.06,
    },
    "sectorRules": {
        "accelerating": 0.030,
        "sustained": 0.020,
        "overheated": 0.004,
        "cooling": -0.050,
        "outflow": -0.080,
        "leader": 0.025,
        "hot": 0.018,
        "member": 0.006,
    },
}

STRATEGY_C = {
    "id": "C_defensive_pool",
    "name": "防守策略 C",
    "description": "弱市防守型优选池方案；只追踪候选与后续表现，不影响任何买入账户。",
    "weights": {
        "base": 0.70,
        "resilience": 0.16,
        "stability": 0.08,
        "sector": 0.06,
    },
}


def build_shadow_strategy_candidates(
    signals: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    context_evidence: dict[str, dict[str, Any]],
    latest_price_by_code: dict[str, dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for signal in signals:
        code = str(signal.get("code") or "")
        if not code:
            continue
        grouped.setdefault(code, {})[str(signal.get("agent_name") or "")] = signal

    snapshot_by_code = {str(row.get("code")): row for row in snapshots if row.get("code")}
    rows: list[dict[str, Any]] = []
    for code, by_agent in grouped.items():
        snap = snapshot_by_code.get(code) or {}
        context = context_evidence.get(code) if isinstance(context_evidence.get(code), dict) else {}
        price_row = latest_price_by_code.get(code) if isinstance(latest_price_by_code.get(code), dict) else {}
        quote = context.get("quote") if isinstance(context.get("quote"), dict) else {}
        score, reasons = shadow_score(by_agent, context)
        price = first_number(price_row.get("currentClose"), quote.get("price"), quote.get("last"), snap.get("close"))
        quote_time = first_text(price_row.get("latestQuoteTime"), quote.get("quote_time"), snap.get("trade_date"))
        rows.append(
            {
                "code": code,
                "name": snap.get("name") or quote.get("name") or code,
                "score": round(score, 4),
                "shadowScore": round(score, 4),
                "price": round(price, 3) if price is not None else None,
                "quoteTime": quote_time,
                "reasons": reasons,
                "sector": sector_snapshot(context),
            }
        )

    rows.sort(key=lambda row: (row.get("shadowScore") or 0, row.get("price") is not None), reverse=True)
    for index, row in enumerate(rows[:limit], start=1):
        row["rank"] = index
    return rows[:limit]


def build_defensive_strategy_candidates(
    preferred: list[dict[str, Any]],
    limit: int = 8,
    signals: list[dict[str, Any]] | None = None,
    snapshots: list[dict[str, Any]] | None = None,
    context_evidence: dict[str, dict[str, Any]] | None = None,
    latest_price_by_code: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if signals and snapshots:
        return build_market_defensive_strategy_candidates(
            preferred,
            signals,
            snapshots,
            context_evidence or {},
            latest_price_by_code or {},
            limit=limit,
        )
    return defensive_rows_from_preferred(preferred, limit=limit)


def defensive_rows_from_preferred(preferred: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in preferred:
        code = str(row.get("code") or "")
        if not code:
            continue
        score, reasons = defensive_score(row)
        price = first_number(row.get("currentClose"), row.get("entryClose"))
        rows.append(
            {
                "code": code,
                "name": row.get("name") or code,
                "rank": row.get("rank"),
                "score": round(score, 4),
                "defensiveScore": round(score, 4),
                "price": round(price, 3) if price is not None else None,
                "quoteTime": first_text(row.get("latestQuoteTime"), row.get("entryQuoteTime"), row.get("tradeDate")),
                "reasons": reasons or ["base_defensive_score"],
                "sector": row.get("sectorContext") if isinstance(row.get("sectorContext"), dict) else None,
            }
        )

    rows.sort(key=lambda item: (item.get("defensiveScore") or 0, -(int(item.get("rank") or 99))), reverse=True)
    for index, row in enumerate(rows[:limit], start=1):
        row["rank"] = index
    return rows[:limit]


def build_market_defensive_strategy_candidates(
    preferred: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    context_evidence: dict[str, dict[str, Any]],
    latest_price_by_code: dict[str, dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for signal in signals:
        code = str(signal.get("code") or "")
        agent = str(signal.get("agent_name") or "")
        if code and agent:
            grouped.setdefault(code, {})[agent] = signal

    preferred_by_code = {str(row.get("code")): row for row in preferred if row.get("code")}
    snapshot_by_code = {str(row.get("code")): row for row in snapshots if row.get("code")}
    rows: list[dict[str, Any]] = []
    for code, snap in snapshot_by_code.items():
        row = defensive_market_row(
            code,
            snap,
            grouped.get(code, {}),
            preferred_by_code.get(code),
            context_evidence.get(code) if isinstance(context_evidence.get(code), dict) else {},
            latest_price_by_code.get(code) if isinstance(latest_price_by_code.get(code), dict) else {},
        )
        score, reasons = defensive_score(row)
        price = first_number(row.get("currentClose"), row.get("entryClose"))
        rows.append(
            {
                "code": code,
                "name": row.get("name") or code,
                "rank": row.get("rank"),
                "score": round(score, 4),
                "defensiveScore": round(score, 4),
                "price": round(price, 3) if price is not None else None,
                "quoteTime": first_text(row.get("latestQuoteTime"), row.get("entryQuoteTime"), row.get("tradeDate")),
                "reasons": reasons or ["market_defensive_score"],
                "sector": row.get("sectorContext") if isinstance(row.get("sectorContext"), dict) else sector_snapshot(row.get("context") or {}),
                "candidateUniverse": "market",
            }
        )

    rows.sort(key=lambda item: (item.get("defensiveScore") or 0, -(int(item.get("rank") or 9999))), reverse=True)
    for index, row in enumerate(rows[:limit], start=1):
        row["rank"] = index
    return rows[:limit]


def defensive_market_row(
    code: str,
    snap: dict[str, Any],
    by_agent: dict[str, dict[str, Any]],
    preferred_row: dict[str, Any] | None,
    context: dict[str, Any],
    latest_price: dict[str, Any],
) -> dict[str, Any]:
    quote = context.get("quote") if isinstance(context.get("quote"), dict) else {}
    price = first_number(latest_price.get("currentClose"), quote.get("price"), snap.get("close"))
    latest_pct = first_number(latest_price.get("todayPctChange"), quote.get("pct_change"))
    sector = first_sector_context(latest_price, preferred_row, context)
    base_score = first_number(
        preferred_row.get("finalScore") if preferred_row else None,
        preferred_row.get("adjustedScore") if preferred_row else None,
        preferred_row.get("baseScore") if preferred_row else None,
        defensive_base_signal_score(by_agent),
    )
    return {
        "code": code,
        "name": snap.get("name") or quote.get("name") or (preferred_row or {}).get("name") or code,
        "rank": (preferred_row or {}).get("rank") or 9999,
        "finalScore": base_score,
        "currentClose": price,
        "latestQuoteTime": first_text(latest_price.get("latestQuoteTime"), quote.get("quote_time"), snap.get("trade_date")),
        "tradeDate": snap.get("trade_date"),
        "todayPctChange": latest_pct,
        "basic": {
            "latestPctChange": latest_pct,
            "volatility20d": percent_value(snap.get("volatility_20d")),
            "relativeStrength": first_number(snap.get("relative_strength")),
            "return20d": percent_value(snap.get("return_20d")),
        },
        "states": {
            "isLimitUp": bool(first_bool(latest_price.get("isLimitUp"), snap.get("is_limit_up"))),
            "isSuspended": bool(snap.get("is_suspended")),
        },
        "anomalies": [],
        "sectorContext": sector,
        "context": context,
    }


def defensive_base_signal_score(by_agent: dict[str, dict[str, Any]]) -> float:
    if not by_agent:
        return 0.50
    weights = STRATEGY_B["weights"]
    capital_score = adjusted_agent_score(by_agent.get("capital"))
    return clamp(
        weights["trend"] * adjusted_agent_score(by_agent.get("trend"))
        + weights["capital"] * capital_score
        + weights["liquidity"] * capital_score
        + weights["sentiment"] * adjusted_agent_score(by_agent.get("sentiment"))
        + weights["institutional"] * adjusted_agent_score(by_agent.get("institutional"))
        + weights["information"] * adjusted_agent_score(by_agent.get("information"))
        + weights["fundamental"] * adjusted_agent_score(by_agent.get("fundamental"))
    )


def first_sector_context(
    latest_price: dict[str, Any],
    preferred_row: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    for candidate in (
        latest_price.get("sectorContext"),
        (preferred_row or {}).get("sectorContext"),
        sector_context_from_global_flow(context),
    ):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None


def sector_context_from_global_flow(context: dict[str, Any]) -> dict[str, Any] | None:
    theme = context.get("theme") if isinstance(context.get("theme"), dict) else {}
    flow = theme.get("globalSectorFlow") if isinstance(theme.get("globalSectorFlow"), dict) else {}
    if not flow:
        return None
    return {
        "name": flow.get("primarySector"),
        "flowStatus": flow.get("status"),
        "status": flow.get("status"),
        "role": flow.get("role"),
        "avgPctChange": flow.get("avgPctChange"),
        "upRatio": flow.get("upRatio"),
        "downRatio": flow.get("downRatio"),
        "scoreBonus": flow.get("scoreBonus"),
    }


def percent_value(value: Any) -> float | None:
    number = first_number(value)
    if number is None:
        return None
    return round(number * 100, 4)


def first_bool(*values: Any) -> bool | None:
    for value in values:
        if value is not None:
            return bool(value)
    return None


def defensive_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    base = clamp(first_number(row.get("finalScore"), row.get("adjustedScore"), row.get("baseScore")) or 0)
    basic = row.get("basic") if isinstance(row.get("basic"), dict) else {}
    states = row.get("states") if isinstance(row.get("states"), dict) else {}
    sector = row.get("sectorContext") if isinstance(row.get("sectorContext"), dict) else {}
    anomalies = row.get("anomalies") if isinstance(row.get("anomalies"), list) else []
    latest_pct = first_number(row.get("todayPctChange"), row.get("latestPctChange"), basic.get("latestPctChange"))
    volatility = first_number(basic.get("volatility20d"))
    relative_strength = first_number(basic.get("relativeStrength"))
    return20d = first_number(basic.get("return20d"))
    sector_status = first_text(sector.get("flowStatus"), sector.get("status"))
    sector_avg = first_number(sector.get("avgPctChange"))
    sector_up_ratio = first_number(sector.get("upRatio"))

    score = base * STRATEGY_C["weights"]["base"] + 0.15
    reasons: list[str] = []

    if relative_strength is not None:
        if relative_strength >= 0.70:
            score += 0.060
            reasons.append("relative_resilience")
        elif relative_strength >= 0.58:
            score += 0.030
            reasons.append("moderate_resilience")
        elif relative_strength < 0.50:
            score -= 0.065
            reasons.append("weak_relative_strength_penalty")

    if latest_pct is not None:
        if -2.0 <= latest_pct <= 1.8:
            score += 0.035
            reasons.append("low_intraday_drawdown")
        elif latest_pct < -4.0:
            score -= 0.055
            reasons.append("intraday_breakdown_penalty")
        elif latest_pct >= 7.0:
            score -= 0.080
            reasons.append("intraday_overheat_penalty")

    if volatility is not None:
        if volatility <= 3.5:
            score += 0.030
            reasons.append("low_volatility")
        elif volatility >= 5.5:
            score -= 0.060
            reasons.append("high_volatility_penalty")

    if return20d is not None:
        if return20d >= 28.0:
            score -= 0.045
            reasons.append("short_term_overextension_penalty")
        elif -8.0 <= return20d <= 12.0:
            score += 0.016
            reasons.append("unextended_return")

    if sector_status in {"outflow", "cooling"}:
        score -= 0.200 if sector_status == "outflow" else 0.045
        reasons.append("sector_outflow_penalty" if sector_status == "outflow" else "sector_cooling_penalty")
    elif sector_status in {"divergent", "sustained"}:
        score += 0.020
        reasons.append(f"sector_{sector_status}")
    elif sector_status == "overheated":
        score -= 0.035
        reasons.append("sector_overheated_penalty")

    if sector_avg is not None and sector_avg <= -2.0:
        score -= 0.035
        if "sector_outflow_penalty" not in reasons:
            reasons.append("sector_outflow_penalty")
    if sector_up_ratio is not None and sector_up_ratio < 0.35:
        score -= 0.030
        if "sector_outflow_penalty" not in reasons:
            reasons.append("sector_outflow_penalty")

    if states.get("isLimitUp"):
        score -= 0.070
        reasons.append("limit_up_penalty")
    if states.get("isSuspended"):
        score -= 0.300
        reasons.append("suspended_penalty")
    if any(str(item.get("level") or "") == "danger" for item in anomalies if isinstance(item, dict)):
        score -= 0.080
        reasons.append("danger_anomaly_penalty")

    return clamp(score), list(dict.fromkeys(reasons))


def shadow_score(by_agent: dict[str, dict[str, Any]], context: dict[str, Any]) -> tuple[float, list[str]]:
    weights = STRATEGY_B["weights"]
    capital_score = adjusted_agent_score(by_agent.get("capital"))
    score = (
        weights["trend"] * adjusted_agent_score(by_agent.get("trend"))
        + weights["capital"] * capital_score
        + weights["liquidity"] * capital_score
        + weights["sentiment"] * adjusted_agent_score(by_agent.get("sentiment"))
        + weights["institutional"] * adjusted_agent_score(by_agent.get("institutional"))
        + weights["information"] * adjusted_agent_score(by_agent.get("information"))
        + weights["fundamental"] * adjusted_agent_score(by_agent.get("fundamental"))
    )
    risk_penalty = sum(risk_penalty_for_signal(signal) for signal in by_agent.values() if signal.get("risk_flag"))
    sector_adjustment, sector_reasons = sector_score_adjustment(context)
    score = clamp(score + sector_adjustment - risk_penalty)
    if risk_penalty:
        sector_reasons.append("agent_risk_penalty")
    return score, sector_reasons or ["base_shadow_score"]


def adjusted_agent_score(signal: dict[str, Any] | None) -> float:
    if not signal:
        return 0.50
    score = first_number(signal.get("score"))
    confidence = clamp(first_number(signal.get("confidence")) or 0)
    if score is None:
        return 0.50
    return clamp(0.50 + (score - 0.50) * (0.50 + 0.50 * confidence))


def risk_penalty_for_signal(signal: dict[str, Any]) -> float:
    base = {
        "information": 0.18,
        "fundamental": 0.12,
        "trend": 0.08,
        "capital": 0.08,
        "institutional": 0.08,
        "sentiment": 0.06,
    }.get(str(signal.get("agent_name") or ""), 0.06)
    confidence = clamp(first_number(signal.get("confidence")) or 0)
    return base * (0.50 + 0.50 * confidence)


def sector_score_adjustment(context: dict[str, Any]) -> tuple[float, list[str]]:
    theme = context.get("theme") if isinstance(context.get("theme"), dict) else {}
    quote = context.get("quote") if isinstance(context.get("quote"), dict) else {}
    flow = theme.get("globalSectorFlow") if isinstance(theme.get("globalSectorFlow"), dict) else {}
    rules = STRATEGY_B["sectorRules"]
    status = str(flow.get("status") or "")
    role = str(flow.get("role") or "")
    adjustment = 0.0
    reasons: list[str] = []

    if status in rules:
        adjustment += float(rules[status])
        if status == "outflow":
            reasons.append("sector_outflow_penalty")
        elif status == "cooling":
            reasons.append("sector_cooling_penalty")
        else:
            reasons.append(f"sector_{status}")
    if role in rules:
        adjustment += float(rules[role])
        reasons.append(f"sector_{role}")

    pct_change = first_number(quote.get("pct_change"))
    volume_ratio = first_number(quote.get("volume_ratio"))
    if pct_change is not None and pct_change >= 8.0 and status not in {"accelerating", "sustained"}:
        adjustment -= 0.025
        reasons.append("intraday_overheat_penalty")
    if pct_change is not None and pct_change < 0 and volume_ratio is not None and volume_ratio >= 1.8:
        adjustment -= 0.025
        reasons.append("heavy_drop_penalty")

    return adjustment, reasons


def sector_snapshot(context: dict[str, Any]) -> dict[str, Any] | None:
    theme = context.get("theme") if isinstance(context.get("theme"), dict) else {}
    flow = theme.get("globalSectorFlow") if isinstance(theme.get("globalSectorFlow"), dict) else {}
    if not flow:
        return None
    return {
        "name": flow.get("primarySector"),
        "status": flow.get("status"),
        "role": flow.get("role"),
        "scoreBonus": flow.get("scoreBonus"),
        "recognitionScore": flow.get("recognitionScore"),
    }


def build_strategy_a_candidates(preferred: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(preferred[:limit], start=1):
        rows.append(
            {
                "code": row.get("code"),
                "name": row.get("name") or row.get("code"),
                "rank": int(row.get("rank") or index),
                "score": first_number(row.get("finalScore"), row.get("adjustedScore"), row.get("baseScore")),
                "price": first_number(row.get("currentClose"), row.get("entryClose")),
                "quoteTime": first_text(row.get("latestQuoteTime"), row.get("entryQuoteTime"), row.get("tradeDate")),
                "sector": row.get("sectorContext") if isinstance(row.get("sectorContext"), dict) else None,
            }
        )
    return rows


def update_strategy_comparison(
    path: Path,
    latest_data_date: str | None,
    asset_version: str,
    a_candidates: list[dict[str, Any]],
    b_candidates: list[dict[str, Any]],
    c_candidates: list[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
    latest_price_by_code: dict[str, dict[str, Any]] | None = None,
    max_records: int = 120,
) -> dict[str, Any]:
    if latest_price_by_code is None and isinstance(c_candidates, dict):
        latest_price_by_code = c_candidates
        c_candidates = []
    store = load_strategy_store(path)
    today = latest_data_date or "unknown"
    now = datetime.now().isoformat(timespec="seconds")
    latest_price_by_code = latest_price_by_code or {}
    c_rows = c_candidates if isinstance(c_candidates, list) else []

    for record in store["history"]:
        update_record_outcomes(record, latest_price_by_code)

    record = next((row for row in store["history"] if row.get("date") == today), None)
    if record is None:
        record = build_daily_record(today, asset_version, a_candidates, b_candidates, c_rows, latest_price_by_code, now)
        store["history"].append(record)
    else:
        record["assetVersion"] = asset_version
        record["updatedAt"] = now
        if not isinstance(record.get("A"), list) or not record.get("A"):
            record["A"] = [candidate_record(row, {}) for row in a_candidates]
        if not isinstance(record.get("B"), list) or not record.get("B"):
            record["B"] = [candidate_record(row, {}) for row in b_candidates]
        if (
            not isinstance(record.get("C"), list)
            or not record.get("C")
            or should_reseed_candidate_rows(record.get("C"), c_rows)
            or should_reseed_candidate_universe(record.get("C"), c_rows)
        ):
            record["C"] = [candidate_record(row, {}) for row in c_rows]
        update_record_outcomes(record, latest_price_by_code)
    store["history"].sort(key=lambda row: str(row.get("date") or ""))
    if len(store["history"]) > max_records:
        store["history"] = store["history"][-max_records:]

    store["updatedAt"] = now
    store["latestDataDate"] = today
    store["assetVersion"] = asset_version
    store["latest"] = record
    store["summary"] = comparison_summary(store["history"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return store


def load_strategy_store(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("history", [])
                data.setdefault("strategies", {})
                data["strategies"].update({"A": STRATEGY_A, "B": STRATEGY_B, "C": STRATEGY_C})
                return data
        except json.JSONDecodeError:
            pass
    return {
        "schemaVersion": 2,
        "strategies": {"A": STRATEGY_A, "B": STRATEGY_B, "C": STRATEGY_C},
        "history": [],
        "summary": {},
        "latest": {},
    }


def build_daily_record(
    today: str,
    asset_version: str,
    a_candidates: list[dict[str, Any]],
    b_candidates: list[dict[str, Any]],
    c_candidates: list[dict[str, Any]],
    latest_price_by_code: dict[str, dict[str, Any]],
    now: str,
) -> dict[str, Any]:
    a_rows = [candidate_record(row, latest_price_by_code) for row in a_candidates]
    b_rows = [candidate_record(row, latest_price_by_code) for row in b_candidates]
    c_rows = [candidate_record(row, latest_price_by_code) for row in c_candidates]
    a_codes = {str(row.get("code")) for row in a_rows if row.get("code")}
    b_codes = {str(row.get("code")) for row in b_rows if row.get("code")}
    c_codes = {str(row.get("code")) for row in c_rows if row.get("code")}
    return {
        "date": today,
        "assetVersion": asset_version,
        "updatedAt": now,
        "A": a_rows,
        "B": b_rows,
        "C": c_rows,
        "sharedCodes": sorted(a_codes & b_codes),
        "sharedAllCodes": sorted(a_codes & b_codes & c_codes),
        "onlyA": [row for row in a_rows if row.get("code") not in b_codes],
        "onlyB": [row for row in b_rows if row.get("code") not in a_codes],
        "onlyC": [row for row in c_rows if row.get("code") not in (a_codes | b_codes)],
        "overlapCount": len(a_codes & b_codes),
        "overlapAllCount": len(a_codes & b_codes & c_codes),
        "onlyACount": len(a_codes - b_codes),
        "onlyBCount": len(b_codes - a_codes),
        "onlyCCount": len(c_codes - a_codes - b_codes),
        "outcomes": {
            "A": summarize_outcomes(a_rows),
            "B": summarize_outcomes(b_rows),
            "C": summarize_outcomes(c_rows),
        },
    }


def candidate_record(row: dict[str, Any], latest_price_by_code: dict[str, dict[str, Any]]) -> dict[str, Any]:
    code = str(row.get("code") or "")
    entry_price = first_number(row.get("price"), row.get("entryPrice"))
    quote_time = first_text(row.get("quoteTime"), row.get("entryQuoteTime"))
    selection_date = selection_date_for_candidate(row)
    record = {
        "code": code,
        "name": row.get("name") or code,
        "rank": row.get("rank"),
        "score": round(first_number(row.get("score"), row.get("shadowScore"), row.get("defensiveScore")) or 0, 4),
        "entryPrice": round(entry_price, 3) if entry_price is not None else None,
        "selectionDate": selection_date,
        "selectionPrice": round(entry_price, 3) if entry_price is not None else None,
        "entryQuoteTime": quote_time,
        "sector": row.get("sector") if isinstance(row.get("sector"), dict) else None,
        "reasons": row.get("reasons") if isinstance(row.get("reasons"), list) else [],
    }
    candidate_universe = first_text(row.get("candidateUniverse"))
    if candidate_universe:
        record["candidateUniverse"] = candidate_universe
    update_candidate_outcome(record, latest_price_by_code)
    return record


def update_record_outcomes(record: dict[str, Any], latest_price_by_code: dict[str, dict[str, Any]]) -> None:
    row_sets: dict[str, list[dict[str, Any]]] = {}
    for key in ("A", "B", "C"):
        rows = record.get(key) if isinstance(record.get(key), list) else []
        for row in rows:
            update_candidate_outcome(row, latest_price_by_code)
        row_sets[key] = rows
        record.setdefault("outcomes", {})[key] = summarize_outcomes(rows)
    refresh_record_differences(record, row_sets.get("A", []), row_sets.get("B", []), row_sets.get("C", []))


def refresh_record_differences(
    record: dict[str, Any],
    a_rows: list[dict[str, Any]],
    b_rows: list[dict[str, Any]],
    c_rows: list[dict[str, Any]],
) -> None:
    a_codes = {str(row.get("code")) for row in a_rows if row.get("code")}
    b_codes = {str(row.get("code")) for row in b_rows if row.get("code")}
    c_codes = {str(row.get("code")) for row in c_rows if row.get("code")}
    record["sharedCodes"] = sorted(a_codes & b_codes)
    record["sharedAllCodes"] = sorted(a_codes & b_codes & c_codes)
    record["onlyA"] = [row for row in a_rows if str(row.get("code") or "") not in b_codes]
    record["onlyB"] = [row for row in b_rows if str(row.get("code") or "") not in a_codes]
    record["onlyC"] = [row for row in c_rows if str(row.get("code") or "") not in (a_codes | b_codes)]
    record["overlapCount"] = len(a_codes & b_codes)
    record["overlapAllCount"] = len(a_codes & b_codes & c_codes)
    record["onlyACount"] = len(a_codes - b_codes)
    record["onlyBCount"] = len(b_codes - a_codes)
    record["onlyCCount"] = len(c_codes - a_codes - b_codes)


def update_candidate_outcome(row: dict[str, Any], latest_price_by_code: dict[str, dict[str, Any]]) -> None:
    code = str(row.get("code") or "")
    latest = latest_price_by_code.get(code) if isinstance(latest_price_by_code.get(code), dict) else {}
    if not row.get("selectionDate"):
        row["selectionDate"] = selection_date_for_candidate(row)
    if row.get("selectionPrice") is None and row.get("entryPrice") is not None:
        row["selectionPrice"] = row.get("entryPrice")
    for key, value in fixed_candidate_returns(row, latest).items():
        row.setdefault(key, value)
    latest_price = first_number(latest.get("currentClose"), latest.get("price"), row.get("latestPrice"))
    entry_price = first_number(row.get("entryPrice"))
    if latest_price is None or entry_price is None or entry_price <= 0:
        return
    latest_quote_time = first_text(latest.get("latestQuoteTime"), latest.get("quoteTime"), row.get("latestQuoteTime"), row.get("entryQuoteTime"))
    entry_quote_time = first_text(row.get("entryQuoteTime"))
    if latest_quote_time and entry_quote_time and quote_time_not_after(latest_quote_time, entry_quote_time):
        if latest_price == entry_price:
            row.pop("latestPrice", None)
            row.pop("latestQuoteTime", None)
            row.pop("gainPct", None)
        return
    if latest_quote_time and entry_quote_time and latest_quote_time == entry_quote_time and latest_price == entry_price:
        row.pop("latestPrice", None)
        row.pop("latestQuoteTime", None)
        row.pop("gainPct", None)
        return
    row["latestPrice"] = round(latest_price, 3)
    row["latestQuoteTime"] = latest_quote_time
    row["gainPct"] = round((latest_price / entry_price - 1) * 100, 2)


def summarize_outcomes(rows: list[dict[str, Any]], default_period: int = 1) -> dict[str, Any]:
    periods = {
        f"{period}d": summarize_outcome_period(rows, period)
        for period in TARGET_RETURN_PERIODS
    }
    selected = periods[f"{default_period}d"]
    return {
        **selected,
        "period": default_period,
        "periods": periods,
    }


def summarize_outcome_period(rows: list[dict[str, Any]], period: int) -> dict[str, Any]:
    key = f"return{period}d"
    gains = [float(row[key]) for row in rows if row.get(key) is not None]
    if not gains:
        # Keep legacy snapshots readable without letting them affect new fixed-period rows.
        if period == 1:
            gains = [float(row["gainPct"]) for row in rows if row.get("gainPct") is not None and row.get("return1d") is None]
            key = "gainPct"
        if not gains:
            return {"period": period, "trackedCount": 0, "avgGainPct": None, "winRatePct": None, "best": None, "worst": None}
    best = max(rows, key=lambda row: float(row.get(key) if row.get(key) is not None else -9999))
    worst = min(rows, key=lambda row: float(row.get(key) if row.get(key) is not None else 9999))
    return {
        "period": period,
        "trackedCount": len(gains),
        "avgGainPct": round(mean(gains), 2),
        "winRatePct": round(sum(gain > 0 for gain in gains) / len(gains) * 100, 1),
        "best": compact_candidate(best, gain_key=key),
        "worst": compact_candidate(worst, gain_key=key),
    }


def comparison_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    a_gains = [row.get("outcomes", {}).get("A", {}).get("periods", {}).get("1d", {}).get("avgGainPct", row.get("outcomes", {}).get("A", {}).get("avgGainPct")) for row in history]
    b_gains = [row.get("outcomes", {}).get("B", {}).get("periods", {}).get("1d", {}).get("avgGainPct", row.get("outcomes", {}).get("B", {}).get("avgGainPct")) for row in history]
    c_gains = [row.get("outcomes", {}).get("C", {}).get("periods", {}).get("1d", {}).get("avgGainPct", row.get("outcomes", {}).get("C", {}).get("avgGainPct")) for row in history]
    a_values = [float(value) for value in a_gains if value is not None]
    b_values = [float(value) for value in b_gains if value is not None]
    c_values = [float(value) for value in c_gains if value is not None]
    a_avg = round(mean(a_values), 2) if a_values else None
    b_avg = round(mean(b_values), 2) if b_values else None
    c_avg = round(mean(c_values), 2) if c_values else None
    leader = "tie"
    ranked = [(key, value) for key, value in (("A", a_avg), ("B", b_avg), ("C", c_avg)) if value is not None]
    if ranked:
        ranked.sort(key=lambda item: item[1], reverse=True)
        leader = ranked[0][0]
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            leader = "tie"
    return {
        "recordCount": len(history),
        "trackedRecordCount": min((len(values) for values in (a_values, b_values, c_values) if values), default=0),
        "avgGainPctA": a_avg,
        "avgGainPctB": b_avg,
        "avgGainPctC": c_avg,
        "leaderByAvgGain": leader,
        "latestDates": [row.get("date") for row in history[-10:]],
    }


def should_reseed_candidate_rows(existing_rows: Any, incoming_rows: list[dict[str, Any]]) -> bool:
    if not isinstance(existing_rows, list) or not existing_rows or not incoming_rows:
        return False
    existing_times = [quote_time_key(first_text(row.get("entryQuoteTime"))) for row in existing_rows if isinstance(row, dict)]
    incoming_times = [quote_time_key(first_text(row.get("quoteTime"), row.get("entryQuoteTime"))) for row in incoming_rows if isinstance(row, dict)]
    existing_times = [value for value in existing_times if value is not None]
    incoming_times = [value for value in incoming_times if value is not None]
    return bool(existing_times and incoming_times and min(existing_times) > max(incoming_times))


def should_reseed_candidate_universe(existing_rows: Any, incoming_rows: list[dict[str, Any]]) -> bool:
    if not isinstance(existing_rows, list) or not existing_rows or not incoming_rows:
        return False
    incoming_universes = {first_text(row.get("candidateUniverse")) for row in incoming_rows if isinstance(row, dict)}
    incoming_universes.discard(None)
    if "market" not in incoming_universes:
        return False
    existing_universes = {first_text(row.get("candidateUniverse")) for row in existing_rows if isinstance(row, dict)}
    existing_universes.discard(None)
    return "market" not in existing_universes


def quote_time_not_after(latest_quote_time: str, entry_quote_time: str) -> bool:
    latest_key = quote_time_key(latest_quote_time)
    entry_key = quote_time_key(entry_quote_time)
    return latest_key is not None and entry_key is not None and latest_key <= entry_key


def quote_time_key(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits if len(digits) >= 14 else None


def compact_candidate(row: dict[str, Any], gain_key: str = "gainPct") -> dict[str, Any]:
    return {
        "code": row.get("code"),
        "name": row.get("name"),
        "gainPct": row.get(gain_key),
        "rank": row.get("rank"),
    }


def selection_date_for_candidate(row: dict[str, Any]) -> str | None:
    value = first_text(
        row.get("selectionDate"),
        row.get("entryDate"),
        row.get("quoteTime"),
        row.get("entryQuoteTime"),
    )
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return value[:10]


def candidate_return_pct(entry_price: object, close: object) -> float | None:
    entry = first_number(entry_price)
    value = first_number(close)
    if entry is None or entry <= 0 or value is None or value <= 0:
        return None
    return round((value / entry - 1) * 100, 2)


def fixed_candidate_returns(row: dict[str, Any], latest: dict[str, Any]) -> dict[str, float]:
    daily_closes = latest.get("dailyCloses") if isinstance(latest.get("dailyCloses"), dict) else {}
    selection_day = selection_date_for_candidate(row) or ""
    future_dates = sorted(str(day)[:10] for day in daily_closes if str(day)[:10] > selection_day)
    result: dict[str, float] = {}
    for period in TARGET_RETURN_PERIODS:
        if len(future_dates) < period:
            continue
        value = candidate_return_pct(row.get("selectionPrice", row.get("entryPrice")), daily_closes[future_dates[period - 1]])
        if value is not None:
            result[f"return{period}d"] = value
    return result


def first_number(*values: Any) -> float | None:
    for value in values:
        try:
            if value is None:
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return None
