"""Collect US overnight sector context and merge it into A-share evidence."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

US_SECTOR_ETFS = (
    {"symbol": "SOXX", "name": "半导体", "keywords": ["半导体", "芯片", "CPO", "光模块", "AI硬件", "算力"]},
    {"symbol": "SMH", "name": "半导体设备", "keywords": ["半导体", "设备", "先进封装", "存储芯片"]},
    {"symbol": "XLK", "name": "科技", "keywords": ["科技", "软件", "人工智能", "算力", "数据中心"]},
    {"symbol": "XLC", "name": "通信传媒", "keywords": ["传媒", "游戏", "通信", "互联网"]},
    {"symbol": "XLY", "name": "可选消费", "keywords": ["消费电子", "汽车", "零售", "可选消费"]},
    {"symbol": "XLE", "name": "能源", "keywords": ["石油", "油气", "煤炭", "能源"]},
    {"symbol": "XLF", "name": "金融", "keywords": ["银行", "证券", "保险", "金融"]},
    {"symbol": "XLV", "name": "医药", "keywords": ["医药", "创新药", "医疗"]},
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect US overnight sector context for pre-open ranking.")
    parser.add_argument("--date", type=str, required=True, help="A-share run date as YYYYMMDD.")
    parser.add_argument("--context-evidence", type=Path, required=True, help="Context evidence JSON to enrich.")
    parser.add_argument("--out", type=Path, required=True, help="Overnight context report output.")
    parser.add_argument("--merge-context", action="store_true", help="Write overnight sector matches back into context evidence.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    context_path = resolve_path(args.context_evidence)
    out_path = resolve_path(args.out)
    context = load_json(context_path) if context_path.exists() else {}
    report = collect_overnight_context(args.date)
    if args.merge_context and isinstance(context, dict):
        context_path.write_text(
            json.dumps(apply_overnight_context(context, report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status={report.get('status')}")
    print(f"sectors={len(report.get('sectors') or [])}")
    print(f"out={display_path(out_path)}")


def collect_overnight_context(run_date: str) -> dict[str, Any]:
    sectors: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in US_SECTOR_ETFS:
        try:
            pct_change = fetch_yahoo_daily_pct_change(str(item["symbol"]))
        except Exception as error:
            errors.append(f"{item['symbol']}:{error}")
            continue
        sectors.append(
            {
                "symbol": item["symbol"],
                "name": item["name"],
                "pctChange": pct_change,
                "status": sector_status(pct_change),
                "scoreBonus": sector_score_bonus(pct_change),
                "keywords": item["keywords"],
            }
        )
    sectors.sort(key=lambda row: row.get("pctChange") or 0, reverse=True)
    return {
        "runDate": run_date,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "status": "ok" if sectors else "degraded",
        "source": "Yahoo Finance sector ETF daily change",
        "sectors": sectors,
        "errors": errors[:20],
    }


def apply_overnight_context(context: dict[str, dict[str, Any]], overnight: dict[str, Any]) -> dict[str, dict[str, Any]]:
    enriched = deepcopy(context)
    sectors = [sector for sector in overnight.get("sectors", []) if isinstance(sector, dict)]
    for code, item in enriched.items():
        theme = item.get("theme") if isinstance(item.get("theme"), dict) else {}
        theme_names = theme_keywords(theme)
        matches = [
            sector
            for sector in sectors
            if any(keyword and keyword in theme_name for keyword in sector.get("keywords", []) for theme_name in theme_names)
        ]
        if not matches:
            continue
        best = max(matches, key=lambda row: row.get("scoreBonus") or 0)
        theme["overnightUS"] = {
            "status": best.get("status"),
            "sector": best.get("name"),
            "symbol": best.get("symbol"),
            "pctChange": best.get("pctChange"),
            "scoreBonus": best.get("scoreBonus"),
            "matchedSectors": [row.get("name") for row in matches],
        }
        item["theme"] = theme
    return enriched


def fetch_yahoo_daily_pct_change(symbol: str) -> float:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = (payload.get("chart", {}).get("result") or [{}])[0]
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = [float(value) for value in quote.get("close", []) if value is not None]
    if len(closes) < 2:
        raise RuntimeError("not_enough_close_prices")
    return round((closes[-1] / closes[-2] - 1) * 100, 3)


def sector_status(pct_change: float) -> str:
    if pct_change >= 1.0:
        return "positive"
    if pct_change <= -1.0:
        return "negative"
    return "neutral"


def sector_score_bonus(pct_change: float) -> float:
    if pct_change > 0:
        return round(min(0.05, pct_change / 100 * 2), 4)
    return round(max(-0.04, pct_change / 100 * 1.5), 4)


def theme_keywords(theme: dict[str, Any]) -> list[str]:
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
    return list(dict.fromkeys(values))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
