from __future__ import annotations

from ..models import AgentSignal, StockSnapshot
from ..utils import clamp


class CapitalAgent:
    name = "capital"

    def score(self, snapshot: StockSnapshot) -> AgentSignal:
        quote = snapshot.quote_evidence or {}
        theme = snapshot.theme_evidence or {}
        sector_flow = theme.get("globalSectorFlow") if isinstance(theme.get("globalSectorFlow"), dict) else {}
        overnight = theme.get("overnightUS") if isinstance(theme.get("overnightUS"), dict) else {}
        latest_amount = _float(quote.get("amount_yuan"))
        latest_turnover = _float(quote.get("turnover_rate"))
        volume_ratio = _float(quote.get("volume_ratio"))

        blended_amount = max(snapshot.avg_turnover_20d, latest_amount * 0.35) if latest_amount else snapshot.avg_turnover_20d
        blended_turnover = snapshot.turnover_rate_20d
        if latest_turnover > 0:
            blended_turnover = 0.65 * snapshot.turnover_rate_20d + 0.35 * latest_turnover

        turnover_score = clamp((blended_amount - 50_000_000) / 450_000_000)
        activity_score = clamp(blended_turnover / 0.08)
        volume_score = clamp(volume_ratio / 2.5) if volume_ratio > 0 else 0.50
        breakout_confirm = 1.0 if snapshot.return_20d > 0 and blended_amount > 150_000_000 else 0.35
        sector_bonus = min(0.12, max(0.0, _float(sector_flow.get("scoreBonus")) * 3.0))
        sector_penalty = 0.12 if sector_flow.get("status") == "outflow" else 0.06 if sector_flow.get("status") == "cooling" else 0.0
        overnight_adjustment = max(-0.04, min(0.05, _float(overnight.get("scoreBonus"))))
        score = clamp(
            0.38 * turnover_score
            + 0.30 * activity_score
            + 0.17 * breakout_confirm
            + 0.15 * volume_score
            + sector_bonus
            + overnight_adjustment
            - sector_penalty
        )
        risk_flag = blended_turnover > 0.20 or volume_ratio > 4.5
        reason = (
            f"avg_turnover={snapshot.avg_turnover_20d:.0f}, latest_amount={latest_amount:.0f}, "
            f"turnover_rate={blended_turnover:.2%}, volume_ratio={volume_ratio:.2f}, "
            f"sector_flow={sector_flow.get('status') or 'none'}, sector_role={sector_flow.get('role') or 'none'}, "
            f"overnight_us={overnight.get('status') or 'none'}"
        )
        return AgentSignal(
            self.name,
            snapshot.code,
            score,
            0.72 if quote else 0.65,
            risk_flag,
            reason,
            {
                "latest_amount": latest_amount,
                "latest_turnover": latest_turnover,
                "blended_turnover": blended_turnover,
                "volume_ratio": volume_ratio,
                "has_quote_evidence": bool(quote),
                "sector_flow_bonus": sector_bonus,
                "sector_flow_penalty": sector_penalty,
                "overnight_us_adjustment": overnight_adjustment,
            },
        )


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
