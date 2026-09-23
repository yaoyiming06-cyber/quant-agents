from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import StockSnapshot


def load_institutional_evidence(path: Path | str | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    evidence_path = Path(path)
    if not evidence_path.exists():
        return {}
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {str(item["code"]): dict(item) for item in payload if "code" in item}
    return {str(code): dict(value) for code, value in payload.items()}


def load_evidence(path: Path | str | None) -> dict[str, dict[str, Any]]:
    return load_institutional_evidence(path)


def apply_institutional_evidence(
    snapshots: list[StockSnapshot],
    evidence_by_code: dict[str, dict[str, Any]],
) -> list[StockSnapshot]:
    if not evidence_by_code:
        return snapshots
    return [
        replace(snapshot, institutional_evidence=evidence_by_code.get(snapshot.code, {}))
        for snapshot in snapshots
    ]


def apply_context_evidence(
    snapshots: list[StockSnapshot],
    evidence_by_code: dict[str, dict[str, Any]],
) -> list[StockSnapshot]:
    if not evidence_by_code:
        return snapshots
    enriched = []
    for snapshot in snapshots:
        item = evidence_by_code.get(snapshot.code, {})
        news = item.get("news") if isinstance(item.get("news"), dict) else {}
        theme = item.get("theme") if isinstance(item.get("theme"), dict) else {}
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        info_risk = snapshot.info_risk
        if news and news.get("material_risk"):
            info_risk = str(news.get("headline") or news.get("summary") or news.get("polarity") or "news_material_risk")
        enriched.append(
            replace(
                snapshot,
                news_evidence=news,
                theme_evidence=theme,
                quote_evidence=quote,
                info_risk=info_risk,
            )
        )
    return enriched


def write_evidence_template(path: Path | str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    template = {
        "000001": {
            "dragon_tiger": {
                "institution_net_buy": 0,
                "institution_seat_count": 0,
                "top_seat_names": [],
            },
            "northbound": {
                "holding_change_shares": 0,
                "holding_change_pct": 0.0,
                "holding_ratio": 0.0,
            },
            "margin": {
                "financing_balance_change": 0,
                "financing_buy_amount": 0,
            },
            "fund": {
                "fund_holding_change": 0.0,
                "fund_count": 0,
            },
            "manual": {
                "score_adjustment": 0.0,
                "notes": ["example only; replace with sourced evidence"],
            },
        }
    }
    output.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
