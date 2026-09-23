"""Build the local quant dashboard data bundle from current run artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant_agents.market_sources import TencentQuoteClient
from quant_agents.sector_flow import apply_global_sector_flow, build_global_sector_flow
from quant_agents.simulated_trading import update_simulated_trading
from quant_agents.strategy_compare import (
    build_defensive_strategy_candidates,
    build_shadow_strategy_candidates,
    build_strategy_a_candidates,
    update_strategy_comparison,
)
DEFAULT_PLAN_PATH = ROOT / "runs" / "snapshot_plan_with_evidence_20260522.json"
DEFAULT_SNAPSHOT_PATH = ROOT / "data" / "features" / "free_snapshots_20260524.json"
DEFAULT_EVIDENCE_PATH = ROOT / "data" / "evidence" / "institutional_evidence_20260522.json"
DEFAULT_OUT_PATH = ROOT / "web_dashboard" / "data.js"
DEFAULT_HISTORY_PATH = ROOT / "data" / "features" / "preferred_pool_history.json"
DEFAULT_HISTORY_PERIODS_PATH = ROOT / "data" / "features" / "preferred_pool_periods.json"
DEFAULT_TRADE_LEDGER_PATH = ROOT / "data" / "features" / "trade_ledger.json"
DEFAULT_SIMULATED_TRADING_PATH = ROOT / "data" / "features" / "simulated_trading.json"
DEFAULT_SIMULATED_TRADING_A2_PATH = ROOT / "data" / "features" / "simulated_trading_a2.json"
DEFAULT_STRATEGY_COMPARISON_PATH = ROOT / "data" / "features" / "strategy_comparison.json"
SIMULATED_INTRADAY_SLIPPAGE = 0.001


AGENT_LABELS = {
    "trend": "趋势面",
    "capital": "资金面",
    "information": "信息面",
    "fundamental": "基本面",
    "sentiment": "情绪面",
    "institutional": "量化/机构痕迹",
}

MARKET_INDEX_FETCH_SYMBOLS = ("sh000001", "sz399001", "sz399006")
MARKET_INDEX_DISPLAY_SYMBOLS = ("sh000001", "sz399006")


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100, 2)


def money_wan(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value / 10000, 2)


def gain_pct(entry_close: float | None, current_close: float | None) -> float | None:
    if not entry_close or current_close is None:
        return None
    return round((current_close / entry_close - 1) * 100, 2)


def average_gain_pct(rows: list[dict], gain_key: str) -> float | None:
    values = [float(row[gain_key]) for row in rows if row.get(gain_key) is not None]
    return round(mean(values), 2) if values else None


def equal_weight_gain_pct(rows: list[dict]) -> float | None:
    ratios: list[float] = []
    for row in rows:
        entry_close = _positive_float(row.get("entryClose"))
        current_close = _positive_float(row.get("currentClose"))
        if entry_close is not None and current_close is not None:
            ratios.append(current_close / entry_close)
    return round((mean(ratios) - 1) * 100, 2) if ratios else None


def intraday_health_adjustment(row: dict) -> dict:
    today_pct = _number_float(row.get("todayPctChange"))
    current = _positive_float(row.get("currentClose"))
    limit_down = _positive_float(row.get("currentLimitDown"))
    states = row.get("states") if isinstance(row.get("states"), dict) else {}
    sector = row.get("sectorContext") if isinstance(row.get("sectorContext"), dict) else {}
    blockers: list[str] = []
    reasons: list[str] = []
    penalty = 0.0

    near_limit_down = current is not None and limit_down is not None and current <= limit_down + 0.01
    if states.get("isLimitDown") or near_limit_down or (today_pct is not None and today_pct <= -9.8):
        blockers.append("limit_down")
        reasons.append("跌停或接近跌停")
        penalty += 0.30
    elif today_pct is not None and today_pct <= -7.0:
        blockers.append("severe_intraday_drop")
        reasons.append("盘中跌幅过大")
        penalty += 0.18
    elif today_pct is not None and today_pct <= -5.0:
        reasons.append("盘中下跌较重")
        penalty += 0.08
    elif today_pct is not None and today_pct <= -3.0:
        reasons.append("盘中转弱")
        penalty += 0.04

    sector_avg = _number_float(sector.get("avgPctChange"))
    down_ratio = _number_float(sector.get("downRatio"))
    if down_ratio is None:
        up_ratio = _number_float(sector.get("upRatio"))
        down_ratio = 1 - up_ratio if up_ratio is not None else None
    if sector_avg is not None and down_ratio is not None:
        if sector_avg <= -3.0 and down_ratio >= 0.75:
            blockers.append("sector_breakdown")
            reasons.append("板块同步破位")
            penalty += 0.12
        elif sector_avg <= -1.0 and down_ratio >= 0.65:
            reasons.append("板块走弱")
            penalty += 0.05

    basic = row.get("basic") if isinstance(row.get("basic"), dict) else {}
    volume_ratio = _number_float(basic.get("latestVolumeRatio"))
    if today_pct is not None and today_pct < 0 and volume_ratio is not None and volume_ratio >= 1.5:
        reasons.append("放量下跌")
        penalty += 0.04

    level = "danger" if blockers else "warn" if penalty > 0 else "ok"
    return {
        "level": level,
        "label": "盘中破位" if blockers else "盘中转弱" if penalty > 0 else "盘中健康",
        "penalty": round(penalty, 4),
        "blockers": list(dict.fromkeys(blockers)),
        "reasons": reasons,
    }


def apply_intraday_health_adjustments(rows: list[dict]) -> list[dict]:
    for row in rows:
        base_score = _number_float(row.get("baseScore"))
        if base_score is None:
            base_score = _number_float(row.get("finalScore")) or 0.0
        health = intraday_health_adjustment(row)
        adjusted_score = max(0.0, base_score - float(health.get("penalty") or 0))
        row["rawRank"] = row.get("rawRank") or row.get("rank")
        row["baseScore"] = round(base_score, 4)
        row["adjustedScore"] = round(adjusted_score, 4)
        row["intradayHealth"] = health

    rows.sort(key=lambda item: (item.get("adjustedScore") or 0, -(item.get("rawRank") or 99)), reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def evidence_line(code: str, item: dict) -> dict:
    dragon = item.get("dragon_tiger") or {}
    margin = item.get("margin") or {}
    tags: list[str] = []
    notes: list[str] = []
    if dragon:
        tags.append("龙虎榜")
        net = dragon.get("institution_net_buy") or 0
        verb = "净买入" if net >= 0 else "净卖出"
        notes.append(f"机构席位{verb}{money_wan(abs(net))}万元")
    if margin:
        tags.append("融资")
        change = margin.get("financing_balance_change") or 0
        verb = "增加" if change >= 0 else "下降"
        notes.append(f"融资余额{verb}{money_wan(abs(change))}万元")
    return {
        "code": code,
        "tags": tags,
        "brief": "；".join(notes) if notes else "有公开线索",
    }


def build_evidence_summary(evidence: dict, preferred_codes: set[str]) -> dict:
    dragon_codes = [code for code, item in evidence.items() if item.get("dragon_tiger")]
    margin_codes = [code for code, item in evidence.items() if item.get("margin")]
    preferred = [
        evidence_line(code, evidence[code])
        for code in sorted(preferred_codes)
        if evidence.get(code)
    ]
    preview_codes = list(dict.fromkeys([row["code"] for row in preferred] + dragon_codes[:4] + margin_codes[:4]))
    preview = [evidence_line(code, evidence[code]) for code in preview_codes if evidence.get(code)]
    return {
        "totalCodes": len(evidence),
        "dragonTigerCodes": len(dragon_codes),
        "marginCodes": len(margin_codes),
        "preferredCodes": len(preferred),
        "preferred": preferred,
        "preview": preview[:8],
        "note": "公开数据线索仅用于辅助判断，不代表机构实时持仓或买入评级。",
    }


MESSAGE_NEWS_KEYWORDS = (
    "公告",
    "拟",
    "计划",
    "投资",
    "投建",
    "建设",
    "扩产",
    "产能",
    "加产能",
    "回购",
    "减持",
    "增持",
    "股权",
    "冻结",
    "辞职",
    "澄清",
    "传闻",
    "合作",
    "签约",
    "订单",
    "中标",
    "并购",
    "收购",
    "重组",
    "项目",
    "涨价",
    "价格上调",
    "政策",
    "政府",
    "监管",
    "处罚",
    "立案",
    "诉讼",
    "纠纷",
    "破产",
    "产线",
    "发布",
    "财报",
    "业绩",
    "营收",
    "利润",
    "亏损",
    "报案",
    "合同",
    "获批",
    "问询函",
    "需求",
    "HBM",
    "DRAM",
    "三星",
    "海力士",
    "韩国",
    "美光",
    "台积电",
    "英伟达",
)

MARKET_DATA_NEWS_KEYWORDS = (
    "半年线",
    "涨幅",
    "涨超",
    "上涨",
    "下跌",
    "跌停",
    "涨停",
    "走势",
    "主力资金",
    "净流出",
    "净流入",
    "融资客",
    "龙虎榜数据",
    "上榜",
    "收盘价",
    "历史新高",
    "盘中",
    "ETF",
    "个股",
    "成交",
    "换手",
)


def is_message_news_event(event: dict, news: dict | None = None) -> bool:
    title = str(event.get("title") or "")
    summary = str(event.get("summary") or "")
    if not title and not summary and news:
        summary = str(news.get("summary") or news.get("headline") or "")
    text = f"{title} {summary}"
    if not text.strip():
        return False
    if any(keyword in text for keyword in MESSAGE_NEWS_KEYWORDS):
        return True
    if any(keyword in text for keyword in MARKET_DATA_NEWS_KEYWORDS):
        return False
    return False


def build_information_summary(context_evidence: dict, preferred: list[dict]) -> dict:
    preferred_by_code = {stock.get("code"): stock for stock in preferred}
    totals = {
        "codes": 0,
        "eventCodes": 0,
        "events": 0,
        "materialRisks": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "mixed": 0,
    }
    preferred_rows: list[dict] = []
    stock_rows: list[dict] = []
    event_rows: list[dict] = []
    for code, item in context_evidence.items():
        news = item.get("news") if isinstance(item.get("news"), dict) else {}
        if not news:
            continue
        events = news.get("events") if isinstance(news.get("events"), list) else []
        polarity = str(news.get("polarity") or "neutral")
        totals["codes"] += 1
        totals["events"] += len(events)
        totals[polarity if polarity in totals else "neutral"] += 1
        if news.get("material_risk"):
            totals["materialRisks"] += 1
        if events:
            totals["eventCodes"] += 1

        stock = preferred_by_code.get(code)
        name = stock.get("name") if stock else (item.get("quote") or {}).get("name")
        row = _information_row(code, name, news, item.get("theme"))
        if stock:
            row["rank"] = stock.get("rank") or 0
        if row["eventCount"] or row["materialRisk"] or row["polarity"] in ("negative", "mixed"):
            stock_rows.append(row)
            if stock:
                preferred_rows.append(row)
        for event in events[:3]:
            event_rows.append(
                {
                    "code": code,
                    "name": name,
                    "title": event.get("title") or news.get("headline") or news.get("summary"),
                    "source": event.get("source"),
                    "publishTime": event.get("publish_time"),
                    "polarity": event.get("polarity") or polarity,
                    "severity": event.get("severity") or news.get("severity"),
                    "url": event.get("url"),
                }
            )

    def event_weight(row: dict) -> tuple[int, str]:
        severity_rank = {"high": 3, "medium": 2, "low": 1}
        polarity_rank = {"negative": 3, "mixed": 2, "positive": 1, "neutral": 0}
        return (
            severity_rank.get(row.get("severity"), 0) * 10 + polarity_rank.get(row.get("polarity"), 0),
            str(row.get("publishTime") or ""),
        )

    event_rows = sorted(event_rows, key=event_weight, reverse=True)
    stock_rows = sorted(stock_rows, key=lambda row: (row["riskLevel"] == "danger", row["severityRank"], row["eventCount"]), reverse=True)
    preferred_rows = sorted(preferred_rows, key=lambda row: (row["riskLevel"] == "danger", row["severityRank"], row["eventCount"], row["rank"]), reverse=True)
    return {
        "totals": totals,
        "preferred": preferred_rows,
        "stocks": stock_rows[:40],
        "events": event_rows[:12],
        "note": "新闻和公告为免费公开源聚合，关键词分类只做模拟实盘的风险提示。",
    }


def build_market_indices(trade_date: date | None) -> dict:
    try:
        quotes = TencentQuoteClient().fetch_market_indices(MARKET_INDEX_FETCH_SYMBOLS, trade_date=trade_date, use_cache=False)
    except Exception as error:
        return {
            "status": "error",
            "error": str(error),
            "items": [],
            "note": "今日大盘指数抓取失败，网页保留缺失提示。",
        }
    items = [
        {
            "symbol": symbol,
            "name": quote.get("label") or quote.get("name") or symbol,
            "price": _positive_float(quote.get("price")),
            "change": quote.get("change"),
            "pctChange": quote.get("pct_change"),
            "quoteTime": quote.get("quote_time"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "amountYuan": quote.get("amount_yuan"),
        }
        for symbol, quote in quotes.items()
        if symbol in MARKET_INDEX_DISPLAY_SYMBOLS
    ]
    market_amount = sum(
        float((quotes.get(symbol) or {}).get("amount_yuan") or 0)
        for symbol in ("sh000001", "sz399001")
    )
    return {
        "status": "ok" if items else "empty",
        "items": items,
        "marketAmountYuan": round(market_amount, 2) if market_amount > 0 else None,
        "latestQuoteTime": max((str(item.get("quoteTime")) for item in items if item.get("quoteTime")), default=None),
        "note": "指数和成交额来自腾讯财经公开接口，用于大盘环境观察。",
    }


def _information_row(code: str, name: str | None, news: dict, theme: object) -> dict:
    polarity = str(news.get("polarity") or "neutral")
    severity = str(news.get("severity") or "low")
    severity_rank = {"high": 3, "medium": 2, "low": 1}.get(severity, 0)
    events = news.get("events") if isinstance(news.get("events"), list) else []
    message_events = [event for event in events if isinstance(event, dict) and is_message_news_event(event, news)]
    risk_level = "danger" if news.get("material_risk") or severity == "high" else "warn" if polarity in ("negative", "mixed") else "info"
    themes = theme.get("themes") if isinstance(theme, dict) else []
    return {
        "code": code,
        "name": name or code,
        "rank": 0,
        "riskLevel": risk_level,
        "polarity": polarity,
        "severity": severity,
        "severityRank": severity_rank,
        "materialRisk": bool(news.get("material_risk")),
        "score": round(float(news.get("score") or 0), 4),
        "confidence": round(float(news.get("confidence") or 0), 4),
        "headline": news.get("headline") or news.get("summary") or "",
        "summary": news.get("summary") or "",
        "eventCount": len(events),
        "events": events[:5],
        "messageEventCount": len(message_events),
        "messageEvents": message_events[:5],
        "themes": themes or [],
    }


def build_latest_price_map(snapshots: list[dict], context_evidence: dict) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for snap in snapshots:
        code = snap.get("code")
        close = _positive_float(snap.get("close"))
        if code and close is not None:
            latest[code] = {
                "currentClose": round(close, 3),
                "latestDate": snap.get("trade_date"),
                "currentSource": "日K快照",
                "latestQuoteTime": None,
                "todayPctChange": pct(snap.get("pct_change")),
                "currentLimitUp": snap.get("limit_up_price"),
                "currentLimitDown": snap.get("limit_down_price"),
                "isLimitUp": bool(snap.get("is_limit_up")),
                "isLimitDown": bool(snap.get("is_limit_down")),
            }

    for code, item in context_evidence.items():
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        price = _positive_float(quote.get("price"))
        if price is None:
            continue
        quote_time = quote.get("quote_time")
        pct_change = _number_float(quote.get("pct_change"))
        limit_up = _positive_float(quote.get("limit_up"))
        limit_down = _positive_float(quote.get("limit_down"))
        latest[code] = {
            "currentClose": round(price, 3),
            "latestDate": _date_from_quote_time(str(quote_time)) if quote_time else latest.get(code, {}).get("latestDate"),
            "currentSource": "腾讯行情",
            "latestQuoteTime": quote_time,
            "todayPctChange": pct_change,
            "currentLimitUp": limit_up,
            "currentLimitDown": limit_down,
            "isLimitUp": bool((limit_up is not None and price >= limit_up - 0.01) or (pct_change is not None and pct_change >= 9.8)),
            "isLimitDown": bool((limit_down is not None and price <= limit_down + 0.01) or (pct_change is not None and pct_change <= -9.8)),
        }

    return latest


def build_sector_contexts(context_evidence: dict) -> dict[str, dict]:
    code_themes: dict[str, list[str]] = {}
    code_hot_score: dict[str, float | None] = {}
    code_flow_signal: dict[str, dict] = {}
    theme_rows: dict[str, list[dict]] = {}
    for code, item in context_evidence.items():
        if not isinstance(item, dict):
            continue
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        theme = item.get("theme") if isinstance(item.get("theme"), dict) else {}
        themes = context_theme_names(theme)
        if not code or not themes:
            continue
        pct_change = _number_float(quote.get("pct_change"))
        hot_score = _number_float(theme.get("hot_score"))
        flow_signal = theme.get("globalSectorFlow") if isinstance(theme.get("globalSectorFlow"), dict) else {}
        quote_time = quote.get("quote_time")
        code_themes[code] = themes
        code_hot_score[code] = hot_score
        code_flow_signal[code] = flow_signal
        for theme_name in themes:
            theme_rows.setdefault(theme_name, []).append(
                {
                    "code": code,
                    "pctChange": pct_change,
                    "hotScore": hot_score,
                    "quoteTime": quote_time,
                }
            )

    theme_stats: dict[str, dict] = {}
    for theme_name, rows in theme_rows.items():
        pct_values = [float(row["pctChange"]) for row in rows if row.get("pctChange") is not None]
        hot_values = [float(row["hotScore"]) for row in rows if row.get("hotScore") is not None]
        up_count = sum(1 for value in pct_values if value > 0)
        down_count = sum(1 for value in pct_values if value < 0)
        total = len(pct_values)
        theme_stats[theme_name] = {
            "avgPctChange": round(mean(pct_values), 2) if pct_values else None,
            "upRatio": round(up_count / total, 4) if total else None,
            "downRatio": round(down_count / total, 4) if total else None,
            "peerCount": len(rows),
            "hotScore": round(mean(hot_values), 2) if hot_values else None,
        }

    result: dict[str, dict] = {}
    for code, themes in code_themes.items():
        flow_signal = code_flow_signal.get(code) or {}
        primary = str(flow_signal.get("primarySector") or themes[0])
        stats = theme_stats.get(primary) or {}
        result[code] = {
            "primaryTheme": primary,
            "themes": themes,
            "avgPctChange": stats.get("avgPctChange"),
            "upRatio": stats.get("upRatio"),
            "downRatio": stats.get("downRatio"),
            "peerCount": stats.get("peerCount") or 0,
            "hotScore": stats.get("hotScore") if stats.get("hotScore") is not None else code_hot_score.get(code),
            "stockHotScore": code_hot_score.get(code),
            "flowStatus": flow_signal.get("status"),
            "flowRecognitionScore": flow_signal.get("recognitionScore"),
            "flowRole": flow_signal.get("role"),
            "flowScoreBonus": flow_signal.get("scoreBonus"),
            "netFlowRatio": flow_signal.get("netFlowRatio"),
            "flowDelta": flow_signal.get("flowDelta"),
            "flowSource": flow_signal.get("flowSource"),
            "latestQuoteTime": (context_evidence.get(code, {}).get("quote") or {}).get("quote_time")
            if isinstance(context_evidence.get(code, {}).get("quote"), dict)
            else None,
        }
    return result


def context_theme_names(theme: dict) -> list[str]:
    result: list[str] = []
    raw_themes = theme.get("themes")
    if isinstance(raw_themes, list):
        result.extend(str(item) for item in raw_themes if item)
    items = theme.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            nested = item.get("themes")
            if isinstance(nested, list):
                result.extend(str(value) for value in nested if value)
    unique = list(dict.fromkeys(result))
    sector_like = [name for name in unique if is_sector_theme_name(name)]
    return sector_like or unique


def is_sector_theme_name(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    if re.search(r"\d+天\d+板", text):
        return False
    excluded_keywords = ("首板", "连板", "涨停", "跌停", "昨日", "今日", "高标", "炸板", "强势股")
    return not any(keyword in text for keyword in excluded_keywords)


def enrich_history_quotes(
    context_evidence: dict,
    history_path: Path,
    trade_date: date | None,
) -> dict:
    if not history_path.exists() or trade_date is None:
        return context_evidence
    try:
        history_rows = load_json(history_path)
    except Exception:
        return context_evidence
    expected = f"{trade_date:%Y-%m-%d}"
    codes: list[str] = []
    seen: set[str] = set()
    for row in history_rows:
        code = row.get("code") if isinstance(row, dict) else None
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    stale_codes = []
    for code in codes:
        quote = context_evidence.get(code, {}).get("quote") if isinstance(context_evidence.get(code), dict) else None
        quote_day = _quote_day(quote.get("quote_time")) if isinstance(quote, dict) else None
        if quote_day != expected:
            stale_codes.append(code)
    if not stale_codes:
        return context_evidence
    try:
        quotes = TencentQuoteClient().fetch_quotes(stale_codes, trade_date=trade_date, use_cache=False)
    except Exception as error:
        print(f"history quote refresh failed: {error}", file=sys.stderr)
        return context_evidence
    for code, quote in quotes.items():
        context_evidence.setdefault(code, {})["quote"] = quote
    return context_evidence


def apply_latest_history_price(row: dict, latest_price_by_code: dict[str, dict], fallback_date: str | None) -> None:
    latest = latest_price_by_code.get(row.get("code"))
    if latest and not history_price_candidate_is_current(row, latest):
        latest = None
    if latest:
        if latest.get("currentClose") is not None:
            row["currentClose"] = latest["currentClose"]
        row["latestDate"] = latest.get("latestDate") or fallback_date or row.get("latestDate")
        row["currentSource"] = latest.get("currentSource") or row.get("currentSource") or "日K快照"
        row["latestQuoteTime"] = latest.get("latestQuoteTime")
        row["todayPctChange"] = latest.get("todayPctChange")
        row["currentLimitUp"] = latest.get("currentLimitUp")
        row["currentLimitDown"] = latest.get("currentLimitDown")
        row["isLimitUp"] = bool(latest.get("isLimitUp"))
        row["isLimitDown"] = bool(latest.get("isLimitDown"))
        row["sectorContext"] = latest.get("sectorContext") or row.get("sectorContext")
    else:
        row["latestDate"] = fallback_date or row.get("latestDate")
        row.setdefault("currentSource", "历史记录")
        row.setdefault("latestQuoteTime", None)
    row["gainToDate"] = gain_pct(row.get("entryClose"), row.get("currentClose"))


def quote_time_key(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:14].ljust(14, "0") if len(digits) >= 8 else ""


def history_price_candidate_is_current(row: dict, candidate: dict) -> bool:
    existing_key = quote_time_key(row.get("latestQuoteTime"))
    candidate_key = quote_time_key(candidate.get("latestQuoteTime"))
    if existing_key and not candidate_key:
        return False
    return not (existing_key and candidate_key and candidate_key < existing_key)


def history_membership_update_allowed(history_path: Path, incoming_quote_time: str | None) -> bool:
    incoming_key = quote_time_key(incoming_quote_time)
    if not incoming_key:
        return False
    rows = load_json(history_path) if history_path.exists() else []
    active_keys = [quote_time_key(row.get("latestQuoteTime")) for row in rows if row.get("active")]
    latest_active_key = max((key for key in active_keys if key), default="")
    return not latest_active_key or incoming_key >= latest_active_key


def _entry_sort_key(row: dict) -> tuple[str, str]:
    quote_time = re.sub(r"\D", "", str(row.get("entryQuoteTime") or ""))
    if len(quote_time) >= 12:
        return (quote_time[:14].ljust(14, "0"), "0")
    entry_date = re.sub(r"\D", "", str(row.get("entryDate") or ""))
    if len(entry_date) == 8:
        return (f"{entry_date}093500", "1")
    return ("99999999999999", "9")


def history_entry_by_code(history_path: Path) -> dict[str, dict]:
    rows = load_json(history_path) if history_path.exists() else []
    by_code: dict[str, dict] = {}
    for row in rows:
        code = row.get("code")
        if not code or not row.get("active"):
            continue
        current = by_code.get(code)
        if current is None or _entry_sort_key(row) < _entry_sort_key(current):
            by_code[code] = row
    for row in rows:
        code = row.get("code")
        if not code or row.get("active"):
            continue
        current = by_code.get(code)
        if current is not None and active_entry_looks_like_reset_duplicate(current, row):
            by_code[code] = row
    return by_code


def same_moment_reentry_key(by_key: dict[str, dict], code: str, stock: dict, latest_date: str | None) -> str | None:
    stock_quote_key = quote_time_key(stock.get("latestQuoteTime") or stock.get("entryQuoteTime"))
    if not stock_quote_key:
        return None
    stock_entry_day = iso_day(stock.get("entryDate")) or latest_date
    candidates: list[tuple[str, str]] = []
    for key, row in by_key.items():
        if row.get("code") != code or row.get("active"):
            continue
        exit_day = iso_day(row.get("exitDate") or row.get("exitQuoteTime"))
        if stock_entry_day and exit_day and exit_day != stock_entry_day:
            continue
        exit_quote_key = quote_time_key(row.get("exitQuoteTime") or row.get("latestQuoteTime"))
        if exit_quote_key != stock_quote_key:
            continue
        candidates.append((key, _entry_sort_key(row)[0]))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[1])[0]


def active_entry_looks_like_reset_duplicate(active_row: dict, reentry_row: dict) -> bool:
    active_quote_key = quote_time_key(active_row.get("entryQuoteTime") or active_row.get("latestQuoteTime"))
    reentry_exit_key = quote_time_key(reentry_row.get("exitQuoteTime") or reentry_row.get("latestQuoteTime"))
    if not active_quote_key or active_quote_key != reentry_exit_key:
        return False
    active_entry = _positive_float(active_row.get("entryClose"))
    active_current = _positive_float(active_row.get("currentClose"))
    if active_entry is None or active_current is None or round(active_entry, 3) != round(active_current, 3):
        return False
    return _entry_sort_key(reentry_row) < _entry_sort_key(active_row)


def build_entry_context(
    snap: dict,
    latest_price: dict,
    active_entry: dict | None,
    fallback_entry_date: str | None,
) -> dict:
    snap_close = _positive_float(snap.get("close"))
    moment_price = _positive_float(latest_price.get("currentClose")) or snap_close
    entry_date = latest_price.get("latestDate") or fallback_entry_date or snap.get("trade_date")
    entry_source = latest_price.get("currentSource") or "日K快照"
    entry_quote_time = latest_price.get("latestQuoteTime")

    if active_entry:
        stored_entry_close = _positive_float(active_entry.get("entryClose"))
        return {
            "entryDate": active_entry.get("entryDate") or entry_date,
            "entryClose": stored_entry_close or moment_price,
            "entrySource": active_entry.get("entrySource") or "历史入池价",
            "entryQuoteTime": active_entry.get("entryQuoteTime"),
            "legacyEntryClose": active_entry.get("legacyEntryClose"),
        }

    result = {
        "entryDate": entry_date,
        "entryClose": round(moment_price, 3) if moment_price is not None else None,
        "entrySource": entry_source,
        "entryQuoteTime": entry_quote_time,
        "legacyEntryClose": None,
    }
    if active_entry and active_entry.get("entryClose") != result["entryClose"]:
        result["legacyEntryClose"] = active_entry.get("entryClose")
    return result


def update_history(
    preferred: list[dict],
    latest_date: str | None,
    history_path: Path,
    latest_price_by_code: dict[str, dict],
    allow_membership_changes: bool = True,
) -> list[dict]:
    old_rows = load_json(history_path) if history_path.exists() else []
    by_key: dict[str, dict] = {}
    active_key_by_code: dict[str, str] = {}
    for row in old_rows:
        code = row.get("code")
        if not code:
            continue
        key = f"{row.get('entryDate')}:{row.get('entryQuoteTime') or 'noquote'}:{code}"
        while key in by_key:
            key = f"{key}:dup"
        by_key[key] = row
        if row.get("active"):
            current_key = active_key_by_code.get(code)
            if current_key is None or _entry_sort_key(row) < _entry_sort_key(by_key[current_key]):
                active_key_by_code[code] = key

    current_codes = {stock["code"] for stock in preferred}
    if allow_membership_changes:
        for row in by_key.values():
            if row.get("code") not in current_codes:
                if row.get("active"):
                    row["_exitedThisRun"] = True
                row["active"] = False

    for stock in preferred if allow_membership_changes else []:
        key = active_key_by_code.get(stock["code"])
        reentry_key = same_moment_reentry_key(by_key, stock["code"], stock, latest_date)
        if key is not None and reentry_key is not None and active_entry_looks_like_reset_duplicate(by_key.get(key, {}), by_key.get(reentry_key, {})):
            del by_key[key]
            active_key_by_code.pop(stock["code"], None)
            key = reentry_key
        elif key is None:
            key = reentry_key
        row = by_key.get(key, {}) if key else {}
        has_fixed_entry = bool(row.get("entryDate") or row.get("entryQuoteTime") or row.get("entrySource"))
        entry_close = row.get("entryClose") if has_fixed_entry else stock["entryClose"]
        entry_date = row.get("entryDate") if has_fixed_entry else stock["entryDate"]
        entry_source = row.get("entrySource") if has_fixed_entry else stock.get("entrySource")
        entry_quote_time = row.get("entryQuoteTime") if has_fixed_entry else stock.get("entryQuoteTime")
        legacy_entry_close = row.get("legacyEntryClose") or stock.get("legacyEntryClose")
        latest_candidate = {
            "currentClose": stock.get("currentClose"),
            "currentSource": stock.get("currentSource"),
            "latestQuoteTime": stock.get("latestQuoteTime"),
            "todayPctChange": stock.get("todayPctChange"),
            "currentLimitUp": stock.get("currentLimitUp"),
            "currentLimitDown": stock.get("currentLimitDown"),
            "sectorContext": stock.get("sectorContext"),
        }
        current_fields = latest_candidate if history_price_candidate_is_current(row, latest_candidate) else row
        current_close = current_fields.get("currentClose")
        next_row = {
            "entryDate": entry_date,
            "latestDate": latest_date,
            "code": stock["code"],
            "name": stock["name"],
            "rankAtEntry": row.get("rankAtEntry", stock["rank"]),
            "currentRank": stock["rank"],
            "entryScore": row.get("entryScore", stock["finalScore"]),
            "currentScore": stock["finalScore"],
            "entryClose": entry_close,
            "entrySource": entry_source,
            "entryQuoteTime": entry_quote_time,
            "currentClose": current_close,
            "gainToDate": gain_pct(entry_close, current_close),
            "riskCheck": stock["riskCheck"],
            "currentSource": current_fields.get("currentSource"),
            "latestQuoteTime": current_fields.get("latestQuoteTime"),
            "todayPctChange": current_fields.get("todayPctChange"),
            "currentLimitUp": current_fields.get("currentLimitUp"),
            "currentLimitDown": current_fields.get("currentLimitDown"),
            "isLimitUp": bool(current_fields.get("isLimitUp") or stock.get("states", {}).get("isLimitUp")),
            "isLimitDown": bool(current_fields.get("isLimitDown") or stock.get("states", {}).get("isLimitDown")),
            "sectorContext": current_fields.get("sectorContext"),
            "active": True,
        }
        if legacy_entry_close is not None:
            next_row["legacyEntryClose"] = legacy_entry_close
        if key is None:
            key = f"{entry_date}:{entry_quote_time or 'noquote'}:{stock['code']}"
            while key in by_key:
                key = f"{key}:dup"
        by_key[key] = next_row

    for row in by_key.values():
        apply_latest_history_price(row, latest_price_by_code, latest_date)
        if row.pop("_exitedThisRun", False):
            row["exitDate"] = row.get("latestDate") or latest_date
            row["exitPrice"] = row.get("currentClose")
            row["exitSource"] = row.get("currentSource")
            row["exitQuoteTime"] = row.get("latestQuoteTime")

    rows = sorted(
        by_key.values(),
        key=lambda row: (
            bool(row.get("active")),
            row.get("entryDate") or "",
            -((row.get("currentRank") if row.get("active") else row.get("rankAtEntry")) or 99),
        ),
        reverse=True,
    )
    history_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows


def update_history_periods(
    preferred: list[dict],
    period_date: str | None,
    periods_path: Path,
) -> list[dict]:
    old_periods = load_json(periods_path) if periods_path.exists() else []
    by_date = {row.get("date"): row for row in old_periods if row.get("date")}
    date_key = period_date or date.today().isoformat()
    stocks = [
        {
            "rank": stock.get("rank"),
            "code": stock.get("code"),
            "name": stock.get("name"),
            "finalScore": stock.get("finalScore"),
            "targetWeight": stock.get("targetWeight"),
            "riskCheck": stock.get("riskCheck"),
            "entryDate": stock.get("entryDate"),
            "entryClose": stock.get("entryClose"),
            "entrySource": stock.get("entrySource"),
            "entryQuoteTime": stock.get("entryQuoteTime"),
            "legacyEntryClose": stock.get("legacyEntryClose"),
            "currentClose": stock.get("currentClose"),
            "currentSource": stock.get("currentSource"),
            "latestQuoteTime": stock.get("latestQuoteTime"),
            "gainAfterEntry": stock.get("gainAfterEntry"),
            "todayPctChange": stock.get("todayPctChange"),
            "sectorContext": stock.get("sectorContext"),
            "anomalyLevels": sorted({item.get("level") for item in stock.get("anomalies", []) if item.get("level")}),
        }
        for stock in preferred
    ]
    by_date[date_key] = {
        "date": date_key,
        "size": len(stocks),
        "avgScore": round(mean([stock["finalScore"] for stock in stocks if stock.get("finalScore") is not None]), 4)
        if stocks
        else 0,
        "stocks": stocks,
    }
    rows = sorted(by_date.values(), key=lambda row: row.get("date") or "", reverse=True)
    periods_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows


def build_factor_scores(stock: dict | None) -> dict[str, dict]:
    if not stock:
        return {}
    factors: dict[str, dict] = {}
    for agent in stock.get("agents", []):
        key = agent.get("key")
        if not key:
            continue
        factors[key] = {
            "label": agent.get("label") or AGENT_LABELS.get(key, key),
            "score": agent.get("score"),
            "confidence": agent.get("confidence"),
            "riskFlag": bool(agent.get("riskFlag")),
            "reason": agent.get("reason") or "",
        }
    return factors


def entry_mode(row: dict) -> str:
    source = str(row.get("entrySource") or "")
    if "盘前" in source:
        return "preopen"
    if row.get("entryQuoteTime"):
        return "intraday"
    if "原入池" in source or "历史" in source:
        return "legacy_reference"
    return "historical"


def quote_time_iso(value: object) -> str | None:
    text = str(value or "")
    if len(text) != 14 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}T{text[8:10]}:{text[10:12]}:{text[12:14]}+08:00"


def ledger_id(row: dict) -> str:
    entry_date = str(row.get("entryDate") or "unknown").replace("-", "")
    quote_time = re.sub(r"\D", "", str(row.get("entryQuoteTime") or "")) or "noquote"
    return f"{entry_date}:{quote_time}:{row.get('code')}"


def build_fill_context(row: dict) -> dict:
    mode = entry_mode(row)
    intended_price = _positive_float(row.get("entryClose"))
    if mode == "intraday" and row.get("entrySource") == "腾讯行情":
        slippage = SIMULATED_INTRADAY_SLIPPAGE
        return {
            "entryMode": mode,
            "fillStatus": "simulated",
            "fillMethod": "intraday_quote_plus_slippage",
            "fillTime": quote_time_iso(row.get("entryQuoteTime")),
            "slippagePct": slippage,
            "fillPrice": round(intended_price * (1 + slippage), 3) if intended_price is not None else None,
        }
    method = "preopen_reference_pending_0935_confirmation" if mode == "preopen" else "reference_price_only"
    return {
        "entryMode": mode,
        "fillStatus": "reference_only",
        "fillMethod": method,
        "fillTime": None,
        "slippagePct": 0.0,
        "fillPrice": intended_price,
    }


def update_trade_ledger(
    history_rows: list[dict],
    preferred: list[dict],
    ledger_path: Path,
    asset_version: str,
    latest_data_date: str | None,
) -> dict:
    old_data = load_json(ledger_path) if ledger_path.exists() else {}
    old_records = old_data.get("records", []) if isinstance(old_data, dict) else old_data
    old_by_id = {row.get("ledgerId"): row for row in old_records if row.get("ledgerId")}
    preferred_by_code = {row.get("code"): row for row in preferred}

    records: list[dict] = []
    for row in history_rows:
        record_id = ledger_id(row)
        old = old_by_id.get(record_id, {})
        stock = preferred_by_code.get(row.get("code"))
        fill = old if old.get("fillStatus") == "simulated" else build_fill_context(row)
        intended_price = _positive_float(old.get("intendedPrice")) or _positive_float(row.get("entryClose"))
        fill_price = _positive_float(fill.get("fillPrice"))
        current_price = _positive_float(row.get("currentClose"))
        active = bool(row.get("active"))
        factor_scores = old.get("factorScores") or build_factor_scores(stock)
        current_factor_scores = build_factor_scores(stock)
        signal_time = quote_time_iso(row.get("entryQuoteTime")) or old.get("signalTime")
        if not signal_time and row.get("entryDate"):
            signal_time = f"{row.get('entryDate')}T09:35:00+08:00"

        record = {
            "ledgerId": record_id,
            "code": row.get("code"),
            "name": row.get("name"),
            "active": active,
            "entryDate": row.get("entryDate"),
            "signalTime": signal_time,
            "entryMode": fill.get("entryMode") or entry_mode(row),
            "entrySource": row.get("entrySource"),
            "entryQuoteTime": row.get("entryQuoteTime"),
            "intendedPrice": intended_price,
            "fillStatus": fill.get("fillStatus"),
            "fillMethod": fill.get("fillMethod"),
            "fillTime": fill.get("fillTime"),
            "fillPrice": fill_price,
            "slippagePct": fill.get("slippagePct", 0.0),
            "targetWeight": stock.get("targetWeight") if stock else old.get("targetWeight"),
            "entryScore": old.get("entryScore", row.get("entryScore")),
            "currentScore": row.get("currentScore"),
            "factorScores": factor_scores,
            "currentFactorScores": current_factor_scores,
            "reason": stock.get("reason") if stock else old.get("reason", ""),
            "riskFlags": stock.get("riskFlags", []) if stock else old.get("riskFlags", []),
            "currentPrice": current_price,
            "currentSource": row.get("currentSource"),
            "currentPriceTime": quote_time_iso(row.get("latestQuoteTime")),
            "currentPctChange": row.get("todayPctChange"),
            "currentLimitUp": row.get("currentLimitUp"),
            "currentLimitDown": row.get("currentLimitDown"),
            "isLimitUp": bool(row.get("isLimitUp")),
            "isLimitDown": bool(row.get("isLimitDown")),
            "currentStates": {
                "isLimitUp": bool(row.get("isLimitUp")),
                "isLimitDown": bool(row.get("isLimitDown")),
            },
            "sectorContext": row.get("sectorContext"),
            "latestDate": row.get("latestDate") or latest_data_date,
            "benchmarkPnlPct": gain_pct(intended_price, current_price),
            "simulatedPnlPct": gain_pct(fill_price, current_price) if fill.get("fillStatus") == "simulated" else None,
            "lastAssetVersion": asset_version,
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        }
        if not active:
            exit_price = _positive_float(old.get("exitPrice")) or _positive_float(row.get("exitPrice"))
            record["exitDate"] = old.get("exitDate") or row.get("exitDate")
            record["exitTime"] = old.get("exitTime") or quote_time_iso(row.get("exitQuoteTime"))
            record["exitPrice"] = exit_price
            record["exitSource"] = old.get("exitSource") or row.get("exitSource")
            record["exitReason"] = old.get("exitReason") or "not_selected_or_rebalanced"
            record["exitStatus"] = old.get("exitStatus") or "marked_exit"
        records.append(record)

    records.sort(
        key=lambda row: (
            bool(row.get("active")),
            str(row.get("entryDate") or ""),
            float(row.get("currentScore") or row.get("entryScore") or 0),
        ),
        reverse=True,
    )
    active_records = [row for row in records if row.get("active")]
    ledger = {
        "schemaVersion": 1,
        "assetVersion": asset_version,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "records": len(records),
            "active": len(active_records),
            "closed": len(records) - len(active_records),
            "referenceOnly": sum(1 for row in records if row.get("fillStatus") == "reference_only"),
            "simulatedFills": sum(1 for row in records if row.get("fillStatus") == "simulated"),
            "activeBenchmarkPnl": average_gain_pct(active_records, "benchmarkPnlPct"),
            "activeSimulatedPnl": average_gain_pct(active_records, "simulatedPnlPct"),
        },
        "records": records,
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    version_dir = ledger_path.parent / "trade_ledger_versions"
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / f"trade_ledger_{asset_version}.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ledger


def anomaly_items(decision: dict, snap: dict, evidence: dict, agent_map: dict) -> list[dict]:
    items: list[dict] = []

    for flag in decision.get("risk_flags", []):
        items.append(
            {
                "level": "danger",
                "type": "风控",
                "title": str(flag),
                "reason": "决策层风控标识，需要人工复核后再进入模拟实盘。",
            }
        )

    r20 = snap.get("return_20d") or 0
    r60 = snap.get("return_60d") or 0
    vol20 = snap.get("volatility_20d") or 0
    turnover = snap.get("turnover_rate_20d") or 0
    avg_turnover = snap.get("avg_turnover_20d") or 0
    prev_close = snap.get("prev_close") or 0
    close = snap.get("close") or 0

    if r20 >= 0.35:
        items.append(
            {
                "level": "warn",
                "type": "动量",
                "title": "20日涨幅偏高",
                "reason": f"近20日涨幅约 {pct(r20)}%，趋势强但短线拥挤度上升，适合降低单笔仓位或等待回撤确认。",
            }
        )

    if r60 >= 1.0:
        items.append(
            {
                "level": "warn",
                "type": "动量",
                "title": "60日涨幅过热",
                "reason": f"近60日涨幅约 {pct(r60)}%，中期收益已经较集中，后续更依赖成交量和风险阈值约束。",
            }
        )

    if vol20 >= 0.055:
        items.append(
            {
                "level": "danger",
                "type": "波动",
                "title": "20日波动率高",
                "reason": f"20日波动率约 {pct(vol20)}%，若盘中放量下跌，应触发更严格的止损/降仓规则。",
            }
        )
    elif vol20 >= 0.04:
        items.append(
            {
                "level": "warn",
                "type": "波动",
                "title": "波动率抬升",
                "reason": f"20日波动率约 {pct(vol20)}%，适合继续跟踪但不宜追高加仓。",
            }
        )

    if turnover >= 0.08:
        items.append(
            {
                "level": "info",
                "type": "换手",
                "title": "换手活跃",
                "reason": f"20日平均换手约 {pct(turnover)}%，交易活跃，说明资金关注度高，也代表分歧更大。",
            }
        )
    elif avg_turnover >= 5_000_000_000:
        items.append(
            {
                "level": "info",
                "type": "成交",
                "title": "成交额显著",
                "reason": f"20日平均成交额约 {money_wan(avg_turnover)} 万元，资金容量较好，适合轻量模拟实盘观察。",
            }
        )

    if prev_close > 0 and abs(close / prev_close - 1) >= 0.07:
        direction = "上涨" if close > prev_close else "下跌"
        items.append(
            {
                "level": "warn",
                "type": "异动",
                "title": f"单日{direction}较大",
                "reason": f"最新收盘相对前收盘变化约 {pct(close / prev_close - 1)}%，需要结合公告、龙虎榜和成交量复核。",
            }
        )

    if snap.get("is_limit_up"):
        items.append(
            {
                "level": "danger",
                "type": "交易限制",
                "title": "涨停状态",
                "reason": "最新快照显示涨停，次日成交可得性和滑点风险都需要单独评估。",
            }
        )

    if snap.get("is_limit_down"):
        items.append(
            {
                "level": "danger",
                "type": "交易限制",
                "title": "跌停状态",
                "reason": "最新快照显示跌停，模拟实盘应暂停新增买入并保留风险复核。",
            }
        )

    dragon = evidence.get("dragon_tiger") or {}
    if dragon:
        net_buy = dragon.get("institution_net_buy") or 0
        seat_count = dragon.get("institution_seat_count") or 0
        level = "info" if net_buy >= 0 else "warn"
        verb = "净买入" if net_buy >= 0 else "净卖出"
        items.append(
            {
                "level": level,
                "type": "龙虎榜",
                "title": f"机构席位{verb}",
                "reason": (
                    f"公开龙虎榜估算机构席位{verb}约 {money_wan(abs(net_buy))} 万元，"
                    f"席位数 {seat_count}。这只是公开席位线索，不代表实时持仓。"
                ),
            }
        )

    margin = evidence.get("margin") or {}
    if margin:
        change = margin.get("financing_balance_change") or 0
        level = "info" if change >= 0 else "warn"
        verb = "增加" if change >= 0 else "下降"
        items.append(
            {
                "level": level,
                "type": "融资",
                "title": f"融资余额{verb}",
                "reason": (
                    f"最近融资余额{verb}约 {money_wan(abs(change))} 万元，"
                    f"数据日 {margin.get('latest_date', '-')}; 它反映杠杆资金线索，不等同机构买入。"
                ),
            }
        )

    institutional = agent_map.get("institutional") or {}
    raw = institutional.get("raw_features") or {}
    if raw.get("crowding_risk"):
        items.append(
            {
                "level": "warn",
                "type": "拥挤",
                "title": "模型识别拥挤风险",
                "reason": "机构/量化痕迹代理识别到拥挤风险，应提高止盈纪律并避免加速段追入。",
            }
        )

    if not items:
        items.append(
            {
                "level": "ok",
                "type": "正常",
                "title": "未触发异常",
                "reason": "当前公开快照和模型信号未触发主要异常标签，仍需等待下一交易日验证入池后表现。",
            }
        )

    return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the local quant dashboard data bundle.")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE_PATH)
    parser.add_argument("--context-evidence", type=Path, default=None)
    parser.add_argument("--expand-report", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--history-periods", type=Path, default=DEFAULT_HISTORY_PERIODS_PATH)
    parser.add_argument("--trade-ledger", type=Path, default=DEFAULT_TRADE_LEDGER_PATH)
    parser.add_argument("--sim-trading", type=Path, default=DEFAULT_SIMULATED_TRADING_PATH)
    parser.add_argument("--sim-trading-a2", type=Path, default=DEFAULT_SIMULATED_TRADING_A2_PATH)
    parser.add_argument("--strategy-comparison", type=Path, default=DEFAULT_STRATEGY_COMPARISON_PATH)
    parser.add_argument("--automation-date", type=str, default=None, help="Automation status date as YYYY-MM-DD.")
    parser.add_argument("--no-market-index", action="store_true", help="Skip Tencent market index quote collection.")
    parser.add_argument(
        "--require-fresh-quotes",
        action="store_true",
        help="Fail before writing if current quote timestamps are not fresh for the plan date.",
    )
    parser.add_argument(
        "--min-quote-fresh-ratio",
        type=float,
        default=0.95,
        help="Minimum same-day quote ratio required when --require-fresh-quotes is set.",
    )
    parser.add_argument(
        "--skip-archive-intraday",
        action="store_true",
        help="Skip scanning archived dashboard snapshots when building intraday chart points.",
    )
    parser.add_argument(
        "--skip-stock-chart-cache",
        action="store_true",
        help="Skip daily K-line cache reads when building stock chart data.",
    )
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def default_expand_report_path(snapshot_path: Path) -> Path:
    snapshot_date = _date_digits_from_path(snapshot_path)
    if snapshot_date:
        dated = ROOT / "runs" / f"free_pool_expand_{snapshot_date}.json"
        if dated.exists():
            return dated
    reports = sorted((ROOT / "runs").glob("free_pool_expand_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    return ROOT / "runs" / "free_pool_expand_missing.json"


def _date_digits_from_path(path: Path) -> str | None:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[-8:]


def simulation_execution_time(latest_quote_time: object | None, asset_version: str) -> object:
    return latest_quote_time or asset_version


def build_strategy_a2_config(start_date: str | None = None) -> dict:
    config = {
        "strategyName": "A2",
        "mainlineProbeEnabled": False,
    }
    if start_date is not None:
        config["startDate"] = start_date
    return config


def seed_strategy_state(
    path: Path,
    main_simulated: dict,
    start_date: str | None,
    strategy: str,
    mode: str,
    reason: str,
    message: str,
) -> dict:
    if path.exists():
        return load_json(path)
    equity = _positive_float((main_simulated.get("account") or {}).get("equity")) or 1_000_000.0
    state = {
        "schemaVersion": 1,
        "mode": mode,
        "strategy": strategy,
        "startDate": start_date,
        "config": {
            "strategyName": strategy,
            "startDate": start_date,
        },
        "account": {
            "initialCash": round(equity, 2),
            "cash": round(equity, 2),
            "realizedPnl": 0.0,
            "dayPnlDate": start_date,
            "dayStartEquity": round(equity, 2),
        },
        "positions": [],
        "fills": [],
        "orders": [],
        "events": [
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "kind": "seed",
                "code": "ALL",
                "reason": reason,
                "message": message,
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def seed_strategy_a2_state(path: Path, main_simulated: dict, start_date: str | None) -> dict:
    return seed_strategy_state(
        path,
        main_simulated,
        start_date,
        "A2",
        "strategy_a2_simulation",
        "strategy_a2_start",
        "Strategy A2 control simulation seeded from Strategy A equity.",
    )


def build_strategy_simulation_comparison(strategy_a: dict, strategy_a2: dict) -> dict:
    account_a = strategy_a.get("account") or {}
    account_a2 = strategy_a2.get("account") or {}
    equity_a = _number_float(account_a.get("equity")) or 0.0
    equity_a2 = _number_float(account_a2.get("equity")) or 0.0
    exposure_a = _number_float(account_a.get("exposurePct")) or 0.0
    exposure_a2 = _number_float(account_a2.get("exposurePct")) or 0.0
    positions_a = int(account_a.get("positions") or len(strategy_a.get("positions") or []))
    positions_a2 = int(account_a2.get("positions") or len(strategy_a2.get("positions") or []))
    rows = [
        comparison_rank_row("A", strategy_a),
        comparison_rank_row("A2", strategy_a2),
    ]
    rows.sort(key=lambda row: (_number_float(row.get("equity")) or 0.0), reverse=True)
    pnl_rows = sorted(rows, key=lambda row: (_number_float(row.get("totalPnlPct")) or 0.0), reverse=True)
    result = {
        "leaderByEquity": "A2" if equity_a2 > equity_a else "A" if equity_a > equity_a2 else "tie",
        "leader": rows[0]["strategy"] if rows and rows[0].get("equity") is not None else "tie",
        "leaderByTotalPnlPct": pnl_rows[0]["strategy"] if pnl_rows and pnl_rows[0].get("totalPnlPct") is not None else "tie",
        "ranked": rows,
        "equityDiff": round(equity_a2 - equity_a, 2),
        "totalPnlPctDiff": round((_number_float(account_a2.get("totalPnlPct")) or 0) - (_number_float(account_a.get("totalPnlPct")) or 0), 2),
        "dayPnlPctDiff": round((_number_float(account_a2.get("dayPnlPct")) or 0) - (_number_float(account_a.get("dayPnlPct")) or 0), 2),
        "exposureDiffPct": round(exposure_a2 - exposure_a, 2),
        "positionDiff": positions_a2 - positions_a,
        "A": comparison_account_summary(account_a, strategy_a),
        "A2": comparison_account_summary(account_a2, strategy_a2),
        "latestAAction": latest_filled_action(strategy_a),
        "latestA2Action": latest_filled_action(strategy_a2),
    }
    return result


def comparison_account_summary(account: dict, simulated: dict | None = None) -> dict:
    summary = {
        "equity": account.get("equity"),
        "initialCash": account.get("initialCash"),
        "totalPnl": account.get("totalPnl"),
        "totalPnlPct": account.get("totalPnlPct"),
        "dayPnl": account.get("dayPnl"),
        "dayPnlPct": account.get("dayPnlPct"),
        "exposurePct": account.get("exposurePct"),
        "positions": account.get("positions"),
    }
    summary.update(simulation_statistics(simulated or {"account": account}))
    return summary


def simulation_statistics(simulated: dict) -> dict:
    account = simulated.get("account") or {}
    config = simulated.get("config") or {}
    fills = [
        row
        for row in (simulated.get("fills") or simulated.get("latestFills") or [])
        if isinstance(row, dict) and row.get("status") == "filled"
    ]
    commission = 0.0
    stamp_tax = 0.0
    gross_volume = 0.0
    slippage_cost = 0.0
    slippage_rate = float(config.get("slippagePct") or 0.0) / 100.0
    open_lots: dict[str, list[dict[str, float]]] = {}
    trade_outcomes: list[float] = []

    for fill in fills:
        shares = int(fill.get("shares") or 0)
        price = _number_float(fill.get("price"))
        if shares <= 0 or price is None:
            continue
        gross = _number_float(fill.get("gross")) or price * shares
        fee = _number_float(fill.get("fee")) or 0.0
        stamp = _number_float(fill.get("stampTax")) or 0.0
        commission += max(0.0, fee - stamp)
        stamp_tax += max(0.0, stamp)
        gross_volume += abs(gross)
        if slippage_rate > 0:
            reference_price = price / (1 + slippage_rate) if fill.get("side") == "buy" else price / (1 - slippage_rate)
            slippage_cost += abs(price - reference_price) * shares

        code = str(fill.get("code") or "")
        if not code:
            continue
        if fill.get("side") == "buy":
            open_lots.setdefault(code, []).append(
                {
                    "shares": float(shares),
                    "costPerShare": (gross + fee) / shares,
                }
            )
            continue
        if fill.get("side") != "sell":
            continue
        remaining = shares
        matched_cost = 0.0
        while remaining > 0 and open_lots.get(code):
            lot = open_lots[code][0]
            matched = min(remaining, int(lot["shares"]))
            lot["shares"] -= matched
            remaining -= matched
            matched_cost += matched * lot["costPerShare"]
            if lot["shares"] <= 0:
                open_lots[code].pop(0)
        matched_shares = shares - remaining
        if matched_shares:
            trade_outcomes.append(gross - fee - matched_cost)

    daily_equities = [
        float(row["equity"])
        for row in (simulated.get("dailyReturns") or [])
        if isinstance(row, dict) and _number_float(row.get("equity")) is not None
    ]
    current_equity = _number_float(account.get("equity"))
    if current_equity is not None:
        daily_equities.append(current_equity)
    peak = 0.0
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    for equity in daily_equities:
        peak = max(peak, equity)
        if peak <= 0:
            continue
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        max_drawdown_pct = max(max_drawdown_pct, drawdown / peak * 100)

    gross_profit = sum(value for value in trade_outcomes if value > 0)
    gross_loss = sum(value for value in trade_outcomes if value < 0)
    return {
        "commission": round(commission, 2),
        "stampTax": round(stamp_tax, 2),
        "fees": round(commission + stamp_tax, 2),
        "grossVolume": round(gross_volume, 2),
        "slippageCost": round(slippage_cost, 2),
        "slippagePct": round(slippage_cost / gross_volume * 100, 4) if gross_volume else 0.0,
        "tradeCount": len(fills),
        "completedTrades": len(trade_outcomes),
        "winningTrades": sum(value > 0 for value in trade_outcomes),
        "losingTrades": sum(value < 0 for value in trade_outcomes),
        "winRatePct": round(sum(value > 0 for value in trade_outcomes) / len(trade_outcomes) * 100, 1)
        if trade_outcomes
        else None,
        "profitFactor": round(gross_profit / abs(gross_loss), 2) if gross_loss else None,
        "maxDrawdown": round(max_drawdown, 2),
        "maxDrawdownPct": round(max_drawdown_pct, 2),
    }


def comparison_rank_row(strategy: str, simulated: dict) -> dict:
    account = simulated.get("account") or {}
    row = {
        "strategy": strategy,
        "equity": account.get("equity"),
        "totalPnl": account.get("totalPnl"),
        "totalPnlPct": account.get("totalPnlPct"),
        "dayPnl": account.get("dayPnl"),
        "dayPnlPct": account.get("dayPnlPct"),
        "exposurePct": account.get("exposurePct"),
        "positions": account.get("positions"),
    }
    row.update(simulation_statistics(simulated))
    return row


def latest_filled_action(simulated: dict) -> dict | None:
    fills = simulated.get("latestFills") or simulated.get("fills") or []
    for row in reversed(fills):
        if row.get("status") == "filled":
            return row
    return None


def missing_snapshot_decision_codes(decisions: list[dict], snapshots: list[dict]) -> list[str]:
    snapshot_codes = {str(item.get("code")) for item in snapshots if item.get("code")}
    missing: list[str] = []
    for decision in decisions:
        code = str(decision.get("code") or "")
        if code and code not in snapshot_codes:
            missing.append(code)
    return missing


def output_display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_automation_status(latest_data_date: str | None, root: Path = ROOT, now: datetime | None = None) -> dict:
    day = (latest_data_date or date.today().isoformat()).replace("-", "")
    checked_at = now or datetime.now()
    items = [
        automation_status_item(
            "midday",
            "中午收盘",
            root / "runs" / f"intraday_update_report_{day}.json",
            expected_slot="11:20",
            run_date=day,
            due_time="11:20",
            now=checked_at,
        ),
        automation_status_item(
            "post_close",
            "下午收盘",
            root / "runs" / f"post_close_update_report_{day}.json",
            run_date=day,
            due_time="15:10",
            now=checked_at,
        ),
        automation_status_item(
            "preopen",
            "盘前预评",
            root / "runs" / f"preopen_check_report_{day}.json",
            run_date=day,
            due_time="08:30",
            now=checked_at,
        ),
    ]
    severity = {"error": 4, "missing": 3, "degraded": 2, "skipped": 1, "ok": 0, "pending": -1}
    worst = max(items, key=lambda item: severity.get(item["status"], 0)) if items else {"status": "missing"}
    return {
        "date": latest_data_date,
        "summaryStatus": worst["status"],
        "items": items,
    }


def build_stock_chart_data(
    preferred: list[dict],
    trade_ledger: dict,
    simulated_trading: dict,
    simulated_trading_a2: dict,
    root: Path = ROOT,
    include_archive_intraday: bool = True,
    include_daily_cache: bool = True,
) -> dict:
    codes = stock_chart_codes(preferred, trade_ledger, simulated_trading, simulated_trading_a2)
    if include_archive_intraday:
        intraday_by_code, latest_quote_by_code = intraday_snapshot_rows(codes, root=root)
    else:
        intraday_by_code = {code: {} for code in codes}
        latest_quote_by_code = {}
    merge_current_stock_chart_points(preferred, intraday_by_code, latest_quote_by_code)
    daily_by_code = (
        {
            code: merge_daily_with_intraday_tail(rows, intraday_by_code.get(code, {}))
            for code in codes
            if (rows := daily_chart_rows(code, root=root))
        }
        if include_daily_cache
        else {}
    )
    markers_by_code = stock_trade_markers(simulated_trading, simulated_trading_a2)
    sources = []
    if include_daily_cache:
        sources.append("daily cache")
    if include_archive_intraday:
        sources.append("archived dashboard snapshots")
    sources.append("current dashboard snapshot")
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "source": " + ".join(sources),
        "codes": codes,
        "dailyByCode": daily_by_code,
        "intradayByCode": intraday_by_code,
        "latestQuoteByCode": latest_quote_by_code,
        "markersByCode": markers_by_code,
    }


def stock_chart_codes(
    preferred: list[dict],
    trade_ledger: dict,
    simulated_trading: dict,
    simulated_trading_a2: dict,
) -> list[str]:
    ordered: list[str] = []

    def add(value: object) -> None:
        code = normalise_stock_code(value)
        if code and code not in ordered:
            ordered.append(code)

    for row in preferred:
        add(row.get("code"))
    for row in (trade_ledger or {}).get("records", []):
        add(row.get("code"))
    for simulated in (simulated_trading or {}, simulated_trading_a2 or {}):
        for key in ("positions", "fills", "latestFills", "orders"):
            for row in simulated.get(key, []) if isinstance(simulated.get(key, []), list) else []:
                add(row.get("code"))
    return ordered


def merge_current_stock_chart_points(
    rows: list[dict],
    intraday_by_code: dict[str, dict[str, list[dict]]],
    latest_quote_by_code: dict[str, dict],
) -> None:
    for row in rows:
        code = normalise_stock_code(row.get("code"))
        if not code:
            continue
        point = intraday_point_from_stock_row(row, fallback_stamp=str(row.get("latestQuoteTime") or ""))
        if point is None:
            continue
        day_rows = intraday_by_code.setdefault(code, {}).setdefault(point["date"], [])
        key = (point["date"], point["time"])
        replaced = False
        for index, existing in enumerate(day_rows):
            if (existing.get("date"), existing.get("time")) == key:
                day_rows[index] = point
                replaced = True
                break
        if not replaced:
            day_rows.append(point)
        day_rows.sort(key=lambda item: item["timestamp"])
        latest_quote_by_code[code] = day_rows[-1].get("quote", point.get("quote", {}))


def normalise_stock_code(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 6:
        return None
    return digits[-6:]


def daily_chart_rows(code: str, root: Path = ROOT, limit: int = 180) -> list[dict]:
    source_path = latest_kline_cache_path(code, root)
    if source_path is None:
        return []
    try:
        raw_rows = load_json(source_path)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw_rows, list):
        return []

    normalized: list[dict] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        day = iso_day(row.get("date"))
        close = _positive_float(row.get("close"))
        open_price = _positive_float(row.get("open"))
        high = _positive_float(row.get("high"))
        low = _positive_float(row.get("low"))
        if not day or close is None or open_price is None or high is None or low is None:
            continue
        normalized.append(
            {
                "date": day,
                "open": round(open_price, 3),
                "close": round(close, 3),
                "high": round(high, 3),
                "low": round(low, 3),
                "volume": _number_float(row.get("volume")),
                "amount": _number_float(row.get("amount")),
                "pctChange": _number_float(row.get("pct_change")),
                "change": _number_float(row.get("change")),
                "turnover": _number_float(row.get("turnover")),
            }
        )

    normalized.sort(key=lambda item: item["date"])
    apply_daily_return_fields(normalized)
    closes: list[float] = []
    for row in normalized:
        closes.append(float(row["close"]))
        row["ma"] = {
            "ma5": round(mean(closes[-5:]), 3) if len(closes) >= 5 else None,
            "ma10": round(mean(closes[-10:]), 3) if len(closes) >= 10 else None,
            "ma20": round(mean(closes[-20:]), 3) if len(closes) >= 20 else None,
        }
    return normalized[-limit:]


def latest_kline_cache_path(code: str, root: Path) -> Path | None:
    candidates: list[Path] = []
    for subdir in (
        root / "data" / "cache" / "source" / "mootdx",
        root / "data" / "cache" / "free" / "kline" / "qfq",
    ):
        if subdir.exists():
            candidates.extend(subdir.glob(f"{code}_*.json"))
    if not candidates:
        return None

    def sort_key(path: Path) -> tuple[str, float]:
        digits = re.findall(r"(\d{8})", path.stem)
        end_day = digits[-1] if digits else ""
        return (end_day, path.stat().st_mtime)

    return max(candidates, key=sort_key)


def intraday_snapshot_rows(codes: list[str], root: Path = ROOT) -> tuple[dict[str, dict[str, list[dict]]], dict[str, dict]]:
    wanted = set(codes)
    by_code: dict[str, dict[str, list[dict]]] = {code: {} for code in codes}
    latest_quote_by_code: dict[str, dict] = {}
    archive_dir = root / "web_dashboard" / "archive"
    if not archive_dir.exists():
        return {}, {}

    seen: set[tuple[str, str, str]] = set()
    for path in sorted(archive_dir.glob("data_*.js")):
        snapshot = load_dashboard_archive(path)
        if not isinstance(snapshot, dict):
            continue
        for row in snapshot_stock_rows(snapshot):
            code = normalise_stock_code(row.get("code"))
            if not code or code not in wanted:
                continue
            point = intraday_point_from_stock_row(row, fallback_stamp=path.stem.replace("data_", ""))
            if point is None:
                continue
            key = (code, point["date"], point["time"])
            if key in seen:
                continue
            seen.add(key)
            by_code.setdefault(code, {}).setdefault(point["date"], []).append(point)
            latest_quote_by_code[code] = point["quote"]

    cleaned: dict[str, dict[str, list[dict]]] = {}
    for code, day_map in by_code.items():
        day_rows: dict[str, list[dict]] = {}
        for day, rows in day_map.items():
            rows.sort(key=lambda item: item["timestamp"])
            day_rows[day] = rows
        if day_rows:
            cleaned[code] = day_rows
    return cleaned, latest_quote_by_code


def snapshot_stock_rows(snapshot: dict) -> list[dict]:
    rows: list[dict] = []
    for key in ("preferred", "history"):
        rows.extend(row for row in snapshot.get(key, []) if isinstance(row, dict))
    ledger = snapshot.get("tradeLedger") if isinstance(snapshot.get("tradeLedger"), dict) else {}
    rows.extend(row for row in ledger.get("records", []) if isinstance(row, dict))
    return rows


def merge_daily_with_intraday_tail(daily_rows: list[dict], intraday_by_date: dict[str, list[dict]]) -> list[dict]:
    if not daily_rows or not intraday_by_date:
        return daily_rows
    by_date = {row["date"]: dict(row) for row in daily_rows}
    for day, points in intraday_by_date.items():
        if not points:
            continue
        prices = [float(point["price"]) for point in points if _positive_float(point.get("price")) is not None]
        if not prices:
            continue
        first = points[0]
        last = points[-1]
        quote = last.get("quote") if isinstance(last.get("quote"), dict) else {}
        existing = by_date.get(day, {})
        open_price = _positive_float(quote.get("open")) or _positive_float(existing.get("open")) or prices[0]
        high_values = [
            value
            for value in (
                _positive_float(existing.get("high")),
                _positive_float(quote.get("high")),
                max(prices),
            )
            if value is not None
        ]
        low_values = [
            value
            for value in (
                _positive_float(existing.get("low")),
                _positive_float(quote.get("low")),
                min(prices),
            )
            if value is not None
        ]
        high = max(high_values) if high_values else max(prices)
        low = min(low_values) if low_values else min(prices)
        close = _positive_float(last.get("price")) or prices[-1]
        by_date[day] = {
            **existing,
            "date": day,
            "open": round(open_price, 3),
            "close": round(close, 3),
            "high": round(high, 3),
            "low": round(low, 3),
            "volume": existing.get("volume"),
            "amount": _number_float(quote.get("amountYuan")) or _number_float(existing.get("amount")),
            "pctChange": _number_float(last.get("pctChange")),
            "change": _number_float(last.get("change")),
            "turnover": _number_float(quote.get("turnoverRate")) or _number_float(existing.get("turnover")),
            "source": "system_snapshot",
            "firstSnapshotTime": first.get("time"),
            "lastSnapshotTime": last.get("time"),
        }
    rows = sorted(by_date.values(), key=lambda item: item["date"])
    apply_daily_return_fields(rows)
    closes: list[float] = []
    for row in rows:
        closes.append(float(row["close"]))
        row["ma"] = {
            "ma5": round(mean(closes[-5:]), 3) if len(closes) >= 5 else None,
            "ma10": round(mean(closes[-10:]), 3) if len(closes) >= 10 else None,
            "ma20": round(mean(closes[-20:]), 3) if len(closes) >= 20 else None,
        }
    return rows[-180:]


def apply_daily_return_fields(rows: list[dict]) -> None:
    previous_close: float | None = None
    for row in rows:
        close = _positive_float(row.get("close"))
        if close is not None and previous_close and previous_close > 0:
            change = close - previous_close
            row["change"] = round(change, 3)
            row["pctChange"] = round(change / previous_close * 100, 2)
        else:
            row["change"] = None
            row["pctChange"] = None
        if close is not None:
            previous_close = close


def load_dashboard_archive(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    prefix = "window.QA_DATA = "
    if text.startswith(prefix):
        text = text.removeprefix(prefix).rstrip(";\n")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def intraday_point_from_stock_row(row: dict, fallback_stamp: str) -> dict | None:
    quote = ((row.get("context") or {}).get("quote") or {}) if isinstance(row.get("context"), dict) else {}
    quote_time = str(row.get("latestQuoteTime") or quote.get("quote_time") or row.get("currentPriceTime") or fallback_stamp)
    digits = re.sub(r"\D", "", quote_time)
    if len(digits) < 12:
        return None
    day = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    clock = f"{digits[8:10]}:{digits[10:12]}"
    price = _positive_float(quote.get("price")) or _positive_float(row.get("currentClose")) or _positive_float(row.get("currentPrice"))
    if price is None:
        return None
    pct_change = _number_float(quote.get("pct_change"))
    change = _number_float(quote.get("change") if quote.get("change") is not None else row.get("todayPriceChange"))
    if pct_change is None:
        pct_change = _number_float(row.get("todayPctChange")) or _number_float(row.get("currentPctChange"))
    normalized_quote = {
        "price": round(price, 3),
        "time": clock,
        "quoteTime": digits[:14] if len(digits) >= 14 else digits,
        "high": _number_float(quote.get("high")),
        "low": _number_float(quote.get("low")),
        "open": _number_float(quote.get("open")),
        "turnoverRate": _number_float(quote.get("turnover_rate_pct")),
        "volumeRatio": _number_float(quote.get("volume_ratio")),
        "totalMarketCap100m": _number_float(quote.get("total_market_cap_100m")),
        "floatMarketCap100m": _number_float(quote.get("float_market_cap_100m")),
        "peDynamic": _number_float(quote.get("pe_dynamic")),
        "amountYuan": _number_float(quote.get("amount_yuan")),
        "pctChange": pct_change,
        "change": change,
    }
    return {
        "date": day,
        "time": clock,
        "timestamp": f"{day}T{clock}:00",
        "price": round(price, 3),
        "pctChange": pct_change,
        "change": change,
        "quote": normalized_quote,
    }


def stock_trade_markers(simulated_trading: dict, simulated_trading_a2: dict) -> dict[str, list[dict]]:
    markers: dict[str, list[dict]] = {}
    for strategy, simulated in (("A", simulated_trading or {}), ("A2", simulated_trading_a2 or {})):
        rows = simulated.get("fills") or []
        if not isinstance(rows, list):
            continue
        for fill in rows:
            if not isinstance(fill, dict) or fill.get("status") != "filled":
                continue
            side = str(fill.get("side") or "")
            if side not in {"buy", "sell"}:
                continue
            code = normalise_stock_code(fill.get("code"))
            price = _positive_float(fill.get("price"))
            if not code or price is None:
                continue
            marker_time = fill.get("time") or fill.get("assetVersion") or fill.get("tradeDate")
            marker_day = iso_day(fill.get("tradeDate")) or iso_day(marker_time)
            marker_clock = marker_clock_from_value(marker_time)
            if marker_day is None:
                continue
            markers.setdefault(code, []).append(
                {
                    "strategy": strategy,
                    "side": side,
                    "date": marker_day,
                    "time": marker_clock,
                    "timestamp": f"{marker_day}T{marker_clock}:00" if marker_clock != "-" else marker_day,
                    "price": round(price, 3),
                    "reason": fill.get("reason"),
                }
            )
    for rows in markers.values():
        rows.sort(key=lambda item: (item["date"], item["time"], item["strategy"]))
    return markers


def iso_day(value: object) -> str | None:
    text = str(value or "")
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def marker_clock_from_value(value: object) -> str:
    text = str(value or "")
    if "T" in text:
        clock = text.split("T", 1)[1]
        return clock[:5] if len(clock) >= 5 else "-"
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 12:
        return f"{digits[8:10]}:{digits[10:12]}"
    return "-"


def automation_status_item(
    key: str,
    label: str,
    path: Path,
    expected_slot: str | None = None,
    run_date: str | None = None,
    due_time: str | None = None,
    now: datetime | None = None,
) -> dict:
    if not path.exists():
        if automation_task_pending(run_date, due_time, now):
            return {
                "key": key,
                "label": label,
                "status": "pending",
                "statusText": "待运行",
                "note": f"计划 {due_time}",
                "reportPath": output_display_path(path),
            }
        return {
            "key": key,
            "label": label,
            "status": "missing",
            "statusText": "未运行",
            "note": "未找到运行报告",
            "reportPath": output_display_path(path),
        }
    report = load_json(path)
    status = str(report.get("status") or "missing")
    if status == "skipped" and report.get("reason") == "outside_post_close_window" and automation_task_pending(run_date, due_time, now):
        return {
            "key": key,
            "label": label,
            "status": "pending",
            "statusText": "待运行",
            "note": f"计划 {due_time}",
            "finishedAt": report.get("finished_at"),
            "reportPath": output_display_path(path),
        }
    command_failed = any(
        int(item.get("returncode") or 0) != 0 and not item.get("toleratedFailure")
        for item in report.get("commands", [])
        if isinstance(item, dict)
    )
    tolerated_failure = any(
        int(item.get("returncode") or 0) != 0 and item.get("toleratedFailure")
        for item in report.get("commands", [])
        if isinstance(item, dict)
    )
    if command_failed:
        status = "error"
    elif status == "ok" and tolerated_failure and not deferred_news_only(report):
        status = "degraded"
    candidate_pool_note = None
    remaining_candidates = _non_negative_int(report.get("remaining") or report.get("remainingCandidates"))
    if status == "ok" and (report.get("pool_complete") is False or (remaining_candidates is not None and remaining_candidates > 0)):
        status = "degraded"
        candidate_pool_note = f"候选池未完成：剩余 {remaining_candidates or 0} 支未进入评分"
    forced_repair = bool(report.get("forced")) and status == "ok"
    if status == "ok" and expected_slot and not forced_repair and str(report.get("slot") or "") < expected_slot:
        status = "skipped"
    labels = {
        "ok": "正常",
        "degraded": "降级完成",
        "error": "失败",
        "skipped": "跳过",
        "missing": "未运行",
        "pending": "待运行",
    }
    note = candidate_pool_note or automation_status_note(report) or "报告已生成"
    return {
        "key": key,
        "label": label,
        "status": status if status in labels else "error",
        "statusText": labels.get(status, "失败"),
        "note": str(note),
        "finishedAt": report.get("finished_at"),
        "reportPath": output_display_path(path),
    }


def automation_task_pending(run_date: str | None, due_time: str | None, now: datetime | None) -> bool:
    if not run_date or not due_time or now is None:
        return False
    day = str(run_date).replace("-", "")
    today = now.strftime("%Y%m%d")
    if day > today:
        return True
    if day < today:
        return False
    try:
        hour, minute = (int(part) for part in due_time.split(":", 1))
    except (TypeError, ValueError):
        return False
    return now.hour * 60 + now.minute < hour * 60 + minute


def deferred_news_only(report: dict) -> bool:
    if report.get("news_status") != "deferred":
        return False
    failures = [
        item
        for item in report.get("commands", [])
        if isinstance(item, dict) and int(item.get("returncode") or 0) != 0
    ]
    return bool(failures) and all(
        item.get("toleratedFailure") and "collect-info-evidence" in [str(part) for part in item.get("command", [])] and "--global-news-only" in [str(part) for part in item.get("command", [])]
        for item in failures
    )


def automation_status_note(report: dict) -> str | None:
    if report.get("news_status") == "deferred":
        return "新闻延后补抓；盘前报价/主题/机构/隔夜重排已完成"
    return report.get("reason") or report.get("slot") or report.get("finished_at")


def main() -> None:
    args = parse_args()
    plan_path = resolve_path(args.plan)
    snapshot_path = resolve_path(args.snapshot)
    evidence_path = resolve_path(args.evidence)
    context_evidence_path = resolve_path(args.context_evidence) if args.context_evidence else None
    expand_report_path = resolve_path(args.expand_report) if args.expand_report else default_expand_report_path(snapshot_path)
    out_path = resolve_path(args.out)
    history_path = resolve_path(args.history)
    history_periods_path = resolve_path(args.history_periods)
    trade_ledger_path = resolve_path(args.trade_ledger)
    sim_trading_path = resolve_path(args.sim_trading)
    sim_trading_a2_path = resolve_path(args.sim_trading_a2)
    strategy_comparison_path = resolve_path(args.strategy_comparison)

    plan = load_json(plan_path)
    snapshots = load_json(snapshot_path)
    evidence = load_json(evidence_path)
    context_evidence = load_json(context_evidence_path) if context_evidence_path and context_evidence_path.exists() else {}
    expand_report = load_json(expand_report_path) if expand_report_path.exists() else {}
    context_quote_date = _parse_iso_date(_date_from_quote_time(_latest_quote_time(context_evidence))) or date.today()
    context_evidence = enrich_history_quotes(context_evidence, history_path, context_quote_date)
    global_sector_flow = (
        plan.get("global_sector_flow")
        if isinstance(plan.get("global_sector_flow"), dict)
        else build_global_sector_flow(context_evidence)
    )
    context_evidence = apply_global_sector_flow(context_evidence, global_sector_flow)
    if context_evidence_path:
        context_evidence_path.write_text(json.dumps(context_evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snap_map = {item["code"]: item for item in snapshots}
    latest_price_by_code = build_latest_price_map(snapshots, context_evidence)
    sector_context_by_code = build_sector_contexts(context_evidence)
    for code, sector_context in sector_context_by_code.items():
        latest_price_by_code.setdefault(code, {})["sectorContext"] = sector_context
    active_entries = history_entry_by_code(history_path)
    signal_map: dict[str, dict[str, dict]] = {}
    for signal in plan.get("signals", []):
        signal_map.setdefault(signal["code"], {})[signal["agent_name"]] = signal

    preferred: list[dict] = []
    latest_quote_time = _latest_quote_time(context_evidence)
    fallback_entry_date = _date_from_quote_time(latest_quote_time) or plan.get("trade_date")
    missing_snapshot_codes = missing_snapshot_decision_codes(plan.get("decisions", []), snapshots)
    for decision in plan.get("decisions", []):
        code = decision["code"]
        snap = snap_map.get(code)
        if snap is None:
            continue
        code_evidence = evidence.get(code, {})
        code_context = context_evidence.get(code, {})
        quote = code_context.get("quote") if isinstance(code_context.get("quote"), dict) else {}
        agents = signal_map.get(code, {})
        anomalies = anomaly_items(decision, snap, code_evidence, agents)
        agent_rows = [
            {
                "key": key,
                "label": AGENT_LABELS.get(key, key),
                "score": round((value.get("score") or 0), 4),
                "confidence": round((value.get("confidence") or 0), 4),
                "riskFlag": bool(value.get("risk_flag")),
                "reason": value.get("reason") or "",
            }
            for key, value in sorted(agents.items(), key=lambda kv: list(AGENT_LABELS).index(kv[0]) if kv[0] in AGENT_LABELS else 99)
        ]

        close = snap.get("close")
        latest_price = latest_price_by_code.get(code, {})
        current_close = latest_price.get("currentClose")
        if current_close is None and close is not None:
            current_close = round(close, 3)
        entry_context = build_entry_context(snap, latest_price, active_entries.get(code), fallback_entry_date)
        entry_close = entry_context.get("entryClose")
        stock_row = {
            "rank": len(preferred) + 1,
            "code": code,
            "name": snap.get("name", code),
            "action": decision.get("action", ""),
            "finalScore": round(decision.get("final_score") or 0, 4),
            "targetWeight": round(decision.get("target_weight") or 0, 4),
            "riskCheck": decision.get("risk_check", ""),
            "riskFlags": decision.get("risk_flags", []),
            "reason": decision.get("reason", ""),
            "tradeDate": snap.get("trade_date") or plan.get("trade_date"),
            "entryDate": entry_context.get("entryDate"),
            "entryClose": entry_close,
            "entrySource": entry_context.get("entrySource"),
            "entryQuoteTime": entry_context.get("entryQuoteTime"),
            "currentClose": current_close,
            "currentSource": latest_price.get("currentSource") or "日K快照",
            "latestQuoteTime": latest_price.get("latestQuoteTime"),
            "gainAfterEntry": gain_pct(entry_close, current_close),
            "todayPctChange": latest_price.get("todayPctChange") if latest_price.get("todayPctChange") is not None else quote.get("pct_change"),
            "todayPriceChange": quote.get("change"),
            "currentLimitUp": latest_price.get("currentLimitUp"),
            "currentLimitDown": latest_price.get("currentLimitDown"),
            "sectorContext": sector_context_by_code.get(code),
            "gainStatus": "入池至今",
            "basic": {
                "close": round(close, 3) if close is not None else None,
                "latestPrice": current_close,
                "latestPctChange": latest_price.get("todayPctChange") if latest_price.get("todayPctChange") is not None else quote.get("pct_change"),
                "latestTurnoverRate": quote.get("turnover_rate_pct"),
                "latestVolumeRatio": quote.get("volume_ratio"),
                "prevClose": round(snap.get("prev_close"), 3) if snap.get("prev_close") is not None else None,
                "open": round(snap.get("open_price"), 3) if snap.get("open_price") is not None else None,
                "ma20": round(snap.get("ma20"), 3) if snap.get("ma20") is not None else None,
                "ma60": round(snap.get("ma60"), 3) if snap.get("ma60") is not None else None,
                "return20d": pct(snap.get("return_20d")),
                "return60d": pct(snap.get("return_60d")),
                "volatility20d": pct(snap.get("volatility_20d")),
                "avgTurnover20dWan": money_wan(snap.get("avg_turnover_20d")),
                "turnoverRate20d": pct(snap.get("turnover_rate_20d")),
                "relativeStrength": round(snap.get("relative_strength") or 0, 4),
                "listingDays": snap.get("listing_days"),
                "peRank": round(snap.get("pe_rank") or 0, 4),
                "roeRank": round(snap.get("roe_rank") or 0, 4),
                "cashflowRank": round(snap.get("cashflow_rank") or 0, 4),
            },
            "states": {
                "isSt": bool(snap.get("is_st")),
                "isSuspended": bool(snap.get("is_suspended")),
                "isLimitUp": bool(latest_price.get("isLimitUp") if latest_price else snap.get("is_limit_up")),
                "isLimitDown": bool(latest_price.get("isLimitDown") if latest_price else snap.get("is_limit_down")),
                "isDataStale": bool(snap.get("is_data_stale")),
                "infoRisk": snap.get("info_risk"),
            },
            "agents": agent_rows,
            "evidence": code_evidence,
            "context": {
                "news": code_context.get("news") if isinstance(code_context.get("news"), dict) else None,
                "theme": code_context.get("theme") if isinstance(code_context.get("theme"), dict) else None,
                "quote": quote or None,
            },
            "evidenceSummary": evidence_line(code, code_evidence) if code_evidence else None,
            "anomalies": anomalies,
        }
        if entry_context.get("legacyEntryClose") is not None:
            stock_row["legacyEntryClose"] = entry_context.get("legacyEntryClose")
        preferred.append(
            stock_row
        )

    preferred = apply_intraday_health_adjustments(preferred)
    all_scores = [item["finalScore"] for item in preferred]
    issue_totals = {
        "ok": sum(1 for item in preferred for row in item["anomalies"] if row["level"] == "ok"),
        "info": sum(1 for item in preferred for row in item["anomalies"] if row["level"] == "info"),
        "warn": sum(1 for item in preferred for row in item["anomalies"] if row["level"] == "warn"),
        "danger": sum(1 for item in preferred for row in item["anomalies"] if row["level"] == "danger"),
    }
    anomaly_stocks = {
        "ok": sum(1 for item in preferred if any(row["level"] == "ok" for row in item["anomalies"])),
        "info": sum(1 for item in preferred if any(row["level"] == "info" for row in item["anomalies"])),
        "warn": sum(1 for item in preferred if any(row["level"] == "warn" for row in item["anomalies"])),
        "danger": sum(1 for item in preferred if any(row["level"] == "danger" for row in item["anomalies"])),
    }
    latest_snapshot_date = snapshots[0].get("trade_date") if snapshots else None
    latest_data_date = _date_from_quote_time(latest_quote_time) or latest_snapshot_date
    expected_quote_date = _date_from_quote_time(latest_quote_time) or plan.get("trade_date") or latest_data_date or latest_snapshot_date
    quote_quality = build_quote_quality(context_evidence, preferred, expected_quote_date)
    candidate_pool_quality = build_candidate_pool_quality(expand_report, snapshots)
    if args.require_fresh_quotes and not quote_quality_passes(quote_quality, args.min_quote_fresh_ratio):
        print(
            "stale quote data: "
            f"expected={quote_quality.get('expectedDate')} "
            f"freshRatio={quote_quality.get('freshRatio')} "
            f"preferredStale={quote_quality.get('preferredStaleCount')} "
            f"staleCodes={quote_quality.get('preferredStaleCodes')}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    history_rows = update_history(
        preferred,
        latest_data_date,
        history_path,
        latest_price_by_code,
        allow_membership_changes=history_membership_update_allowed(history_path, latest_quote_time),
    )
    history_periods = update_history_periods(preferred, latest_data_date, history_periods_path)
    preferred_codes = {item["code"] for item in preferred}
    market_indices = {} if args.no_market_index else build_market_indices(_parse_iso_date(latest_data_date))
    pool_gain = equal_weight_gain_pct(preferred)
    asset_version = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_ledger = update_trade_ledger(history_rows, preferred, trade_ledger_path, asset_version, latest_data_date)
    simulated_trading = update_simulated_trading(
        preferred,
        trade_ledger,
        sim_trading_path,
        asset_version,
        latest_data_date,
        quote_quality,
        candidate_pool_quality=candidate_pool_quality,
        market_context=market_indices,
        execution_time=simulation_execution_time(latest_quote_time, asset_version),
    )
    a2_seed = seed_strategy_a2_state(sim_trading_a2_path, simulated_trading, latest_data_date)
    simulated_trading_a2 = update_simulated_trading(
        preferred,
        trade_ledger,
        sim_trading_a2_path,
        asset_version,
        latest_data_date,
        quote_quality,
        candidate_pool_quality=candidate_pool_quality,
        config=build_strategy_a2_config(a2_seed.get("startDate")),
        market_context=market_indices,
        execution_time=simulation_execution_time(latest_quote_time, asset_version),
    )
    strategy_simulation_comparison = build_strategy_simulation_comparison(
        simulated_trading,
        simulated_trading_a2,
    )
    strategy_comparison = update_strategy_comparison(
        strategy_comparison_path,
        latest_data_date,
        asset_version,
        build_strategy_a_candidates(preferred),
        build_shadow_strategy_candidates(plan.get("signals", []), snapshots, context_evidence, latest_price_by_code),
        build_defensive_strategy_candidates(
            preferred,
            signals=plan.get("signals", []),
            snapshots=snapshots,
            context_evidence=context_evidence,
            latest_price_by_code=latest_price_by_code,
        ),
        latest_price_by_code,
    )
    stock_charts = build_stock_chart_data(
        preferred,
        trade_ledger,
        simulated_trading,
        simulated_trading_a2,
        include_archive_intraday=not args.skip_archive_intraday,
        include_daily_cache=not args.skip_stock_chart_cache,
    )
    dashboard_latest_quote_time = latest_dashboard_quote_time(latest_quote_time, stock_charts)

    data = {
        "meta": {
            "title": "A股量化监管台",
            "generatedAt": date.today().isoformat(),
            "assetVersion": asset_version,
            "planDate": plan.get("trade_date"),
            "latestSnapshotDate": latest_snapshot_date,
            "latestQuoteTime": dashboard_latest_quote_time,
            "latestIndexQuoteTime": market_indices.get("latestQuoteTime"),
            "latestDataDate": latest_data_date,
            "contextEvidenceCodes": len(context_evidence),
            "quoteCodes": sum(1 for item in context_evidence.values() if item.get("quote")),
            "themeCodes": sum(1 for item in context_evidence.values() if item.get("theme")),
            "snapshotFileDate": _date_from_path(snapshot_path) or latest_snapshot_date,
            "poolSize": len(snapshots),
            "preferredSize": len(preferred),
            "evidenceCodes": len(evidence),
            "eligibleCandidates": candidate_pool_quality.get("eligibleCandidates"),
            "deepScoredCandidates": candidate_pool_quality.get("deepScoredCandidates"),
            "skippedCandidates": candidate_pool_quality.get("skippedCandidates"),
            "remainingCandidates": candidate_pool_quality.get("remainingCandidates"),
            "poolComplete": candidate_pool_quality.get("poolComplete"),
            "poolCoverageRatio": candidate_pool_quality.get("coverageRatio"),
            "candidateCoverageStatus": candidate_pool_quality.get("status"),
            "candidatePoolQuality": candidate_pool_quality,
            "quoteQuality": quote_quality,
            "dataWarnings": {
                "missingSnapshotDecisionCodes": missing_snapshot_codes,
                "candidatePool": candidate_pool_quality.get("warning"),
            },
            "dataNote": "免费公开数据快照；入池至今涨幅按首次进入优选池时的可得报价或盘前基准价到当前最新价计算。",
        },
        "summary": {
            "avgScore": round(mean(all_scores), 4) if all_scores else 0,
            "maxScore": max(all_scores) if all_scores else 0,
            "minScore": min(all_scores) if all_scores else 0,
            "anomalyTotals": issue_totals,
            "anomalyStocks": anomaly_stocks,
            "avgReturn20d": round(mean(item["basic"]["return20d"] for item in preferred), 2) if preferred else 0,
            "avgReturn60d": round(mean(item["basic"]["return60d"] for item in preferred), 2) if preferred else 0,
            "poolGain": pool_gain,
            "poolGainMethod": "等权组合收益：mean(当前价 / 入池价 - 1)",
            "poolGainUp": sum(1 for item in preferred if (item.get("gainAfterEntry") or 0) > 0),
            "poolGainDown": sum(1 for item in preferred if (item.get("gainAfterEntry") or 0) < 0),
        },
        "evidenceSummary": build_evidence_summary(evidence, preferred_codes),
        "informationSummary": build_information_summary(context_evidence, preferred),
        "marketIndices": market_indices,
        "globalSectorFlow": global_sector_flow,
        "automationStatus": build_automation_status(args.automation_date or latest_data_date),
        "preferred": preferred,
        "history": history_rows,
        "historyPeriods": history_periods,
        "tradeLedger": trade_ledger,
        "simulatedTrading": simulated_trading,
        "simulatedTradingA2": simulated_trading_a2,
        "strategySimulationComparison": strategy_simulation_comparison,
        "strategyComparison": strategy_comparison,
        "stockCharts": stock_charts,
        "agentLabels": AGENT_LABELS,
    }

    dashboard_payload = "window.QA_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n"
    write_text_atomic(out_path, dashboard_payload)
    archive_dashboard_data(out_path, asset_version, dashboard_payload)
    update_html_asset_versions(out_path.parent, asset_version)
    print(f"Wrote {output_display_path(out_path)} with {len(preferred)} preferred stocks")


def _date_from_path(path: Path) -> str | None:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    if len(digits) < 8:
        return None
    text = digits[-8:]
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _number_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _non_negative_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(number, 0)


def _latest_quote_time(context_evidence: dict) -> str | None:
    values = [
        str(item.get("quote", {}).get("quote_time"))
        for item in context_evidence.values()
        if isinstance(item.get("quote"), dict) and item.get("quote", {}).get("quote_time")
    ]
    return max(values) if values else None


def latest_dashboard_quote_time(context_quote_time: str | None, stock_charts: dict | None) -> str | None:
    values = [str(context_quote_time)] if context_quote_time else []
    latest_quotes = (stock_charts or {}).get("latestQuoteByCode", {})
    if isinstance(latest_quotes, dict):
        for quote in latest_quotes.values():
            if not isinstance(quote, dict):
                continue
            quote_time = quote.get("quoteTime")
            if quote_time:
                values.append(str(quote_time))
    return max(values) if values else None


def _date_from_quote_time(value: str | None) -> str | None:
    if not value or len(value) < 8:
        return None
    text = value[:8]
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _quote_day(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def build_quote_quality(context_evidence: dict, preferred: list[dict], expected_date: str | None) -> dict:
    expected = expected_date[:10] if expected_date else None
    quote_dates: dict[str, int] = {}
    fresh_codes = 0
    stale_codes = 0
    for code, item in context_evidence.items():
        quote = item.get("quote") if isinstance(item, dict) else None
        if not isinstance(quote, dict) or not quote:
            continue
        quote_date = _quote_day(quote.get("quote_time")) or "missing"
        quote_dates[quote_date] = quote_dates.get(quote_date, 0) + 1
        if expected and quote_date == expected:
            fresh_codes += 1
        else:
            stale_codes += 1

    preferred_stale: list[dict] = []
    preferred_fresh = 0
    for row in preferred:
        quote_time = row.get("latestQuoteTime") or ((row.get("context") or {}).get("quote") or {}).get("quote_time")
        quote_date = _quote_day(quote_time)
        if expected and quote_date == expected:
            preferred_fresh += 1
            continue
        preferred_stale.append(
            {
                "code": row.get("code"),
                "name": row.get("name"),
                "quoteDate": quote_date,
                "quoteTime": quote_time,
            }
        )

    quote_count = fresh_codes + stale_codes
    return {
        "expectedDate": expected,
        "quoteDateDistribution": quote_dates,
        "quoteCount": quote_count,
        "freshCount": fresh_codes,
        "staleCount": stale_codes,
        "freshRatio": round(fresh_codes / quote_count, 4) if quote_count else 0,
        "preferredCount": len(preferred),
        "preferredFreshCount": preferred_fresh,
        "preferredStaleCount": len(preferred_stale),
        "preferredStaleCodes": preferred_stale,
    }


def build_candidate_pool_quality(expand_report: dict | None, snapshots: list[dict]) -> dict:
    report = expand_report if isinstance(expand_report, dict) else {}
    snapshot_count = len(snapshots or [])
    eligible = _non_negative_int(report.get("eligible_candidates") or report.get("eligibleCandidates"))
    deep_scored = _non_negative_int(
        report.get("snapshots")
        or report.get("deep_scored_candidates")
        or report.get("deepScoredCandidates")
    )
    if deep_scored is None:
        deep_scored = snapshot_count
    skipped = _non_negative_int(report.get("skipped") or report.get("skipped_candidates") or report.get("skippedCandidates"))
    remaining = _non_negative_int(report.get("remaining") or report.get("remaining_candidates") or report.get("remainingCandidates"))
    if remaining is None and eligible is not None:
        remaining = max(eligible - deep_scored - (skipped or 0), 0)

    explicit_complete = report.get("complete")
    if isinstance(explicit_complete, bool):
        pool_complete = explicit_complete and (remaining in (None, 0))
    elif eligible is not None:
        pool_complete = (remaining or 0) == 0 and deep_scored >= max(eligible - (skipped or 0), 0)
    else:
        pool_complete = False

    if eligible and eligible > 0:
        coverage_ratio = round(min(deep_scored / eligible, 1.0), 4)
    elif snapshot_count:
        coverage_ratio = 1.0 if pool_complete else 0.0
    else:
        coverage_ratio = 0.0

    status = "complete" if pool_complete else "partial" if eligible or remaining or deep_scored else "unknown"
    warning = None
    if status == "partial":
        warning = f"候选池未完成：已深度评分 {deep_scored}/{eligible or '未知'}，剩余 {remaining or 0} 支未进入评分。"
    elif status == "unknown":
        warning = "候选池覆盖状态未知：未找到当天扩展报告。"

    return {
        "status": status,
        "poolComplete": pool_complete,
        "eligibleCandidates": eligible,
        "deepScoredCandidates": deep_scored,
        "skippedCandidates": skipped,
        "remainingCandidates": remaining,
        "coverageRatio": coverage_ratio,
        "coveragePct": round(coverage_ratio * 100, 2),
        "warning": warning,
    }


def quote_quality_passes(quote_quality: dict, min_fresh_ratio: float) -> bool:
    return (
        quote_quality.get("quoteCount", 0) > 0
        and quote_quality.get("freshRatio", 0) >= min_fresh_ratio
        and quote_quality.get("preferredStaleCount", 0) == 0
    )


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def archive_dashboard_data(out_path: Path, version: str, payload: str) -> None:
    archive_dir = out_path.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(archive_dir / f"data_{version}.js", payload)


def update_html_asset_versions(web_dir: Path, version: str) -> None:
    pattern = re.compile(r'(\./(?:styles|data|app)\.(?:css|js))(?:\?v=[^"]*)?')
    for html_path in web_dir.glob("*.html"):
        text = html_path.read_text(encoding="utf-8")
        updated = pattern.sub(rf"\1?v={version}", text)
        if updated != text:
            write_text_atomic(html_path, updated)


def write_text_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


if __name__ == "__main__":
    main()
