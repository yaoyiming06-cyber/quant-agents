from __future__ import annotations

from ..models import AgentSignal, StockSnapshot
from ..utils import clamp


class SentimentAgent:
    name = "sentiment"

    def __init__(self, market_heat: float = 0.55) -> None:
        self.market_heat = market_heat

    def score(self, snapshot: StockSnapshot) -> AgentSignal:
        heat_penalty = 0.15 if self.market_heat > 0.85 and snapshot.return_20d > 0.18 else 0.0
        panic_discount = 0.10 if self.market_heat < 0.25 else 0.0
        theme_boost = 0.0
        theme_reason = ""
        if snapshot.theme_evidence:
            sector_flow = (
                snapshot.theme_evidence.get("globalSectorFlow")
                if isinstance(snapshot.theme_evidence.get("globalSectorFlow"), dict)
                else {}
            )
            overnight = (
                snapshot.theme_evidence.get("overnightUS")
                if isinstance(snapshot.theme_evidence.get("overnightUS"), dict)
                else {}
            )
            rank = _float(snapshot.theme_evidence.get("hot_rank"))
            if 0 < rank <= 20:
                theme_boost = 0.12
            elif 0 < rank <= 100:
                theme_boost = 0.07
            elif snapshot.theme_evidence.get("is_hot"):
                theme_boost = 0.04
            flow_bonus = min(0.08, max(0.0, _float(sector_flow.get("scoreBonus")) * 1.5))
            if sector_flow.get("status") == "overheated":
                flow_bonus *= 0.5
            if sector_flow.get("status") in {"cooling", "outflow"}:
                flow_bonus = 0.0
            theme_boost += flow_bonus
            overnight_adjustment = max(-0.03, min(0.04, _float(overnight.get("scoreBonus"))))
            theme_boost += overnight_adjustment
            theme_reason = (
                f", theme_hot_rank={rank:.0f}" if rank else ", theme_hot=true"
            ) + (
                f", sector_flow={sector_flow.get('status') or 'none'}, "
                f"sector_role={sector_flow.get('role') or 'none'}, "
                f"overnight_us={overnight.get('status') or 'none'}"
            )
        score = clamp(0.50 + 0.25 * snapshot.relative_strength + theme_boost - heat_penalty - panic_discount)
        risk_flag = self.market_heat > 0.90 and snapshot.return_20d > 0.20
        theme_rank = _float(snapshot.theme_evidence.get("hot_rank")) if snapshot.theme_evidence else 0.0
        if snapshot.theme_evidence and snapshot.return_20d > 0.45 and (not theme_rank or theme_rank <= 10):
            risk_flag = True
        reason = f"market_heat={self.market_heat:.2f}, relative_strength={snapshot.relative_strength:.2f}{theme_reason}"
        return AgentSignal(self.name, snapshot.code, score, 0.55 if snapshot.theme_evidence else 0.45, risk_flag, reason)


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
