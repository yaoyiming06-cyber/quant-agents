from __future__ import annotations

from ..models import AgentSignal, StockSnapshot
from ..utils import clamp


class TrendAgent:
    name = "trend"

    def score(self, snapshot: StockSnapshot) -> AgentSignal:
        above_ma20 = snapshot.close > snapshot.ma20
        ma_stack = snapshot.ma20 > snapshot.ma60
        positive_slope = snapshot.ma60_slope > 0
        not_extended = snapshot.close / snapshot.ma20 - 1 <= 0.12 if snapshot.ma20 else False

        momentum = clamp((snapshot.return_20d + 0.05) / 0.20)
        long_momentum = clamp((snapshot.return_60d + 0.08) / 0.30)
        trend_quality = sum([above_ma20, ma_stack, positive_slope, not_extended]) / 4
        score = clamp(0.45 * trend_quality + 0.35 * momentum + 0.20 * long_momentum)

        risk_flag = snapshot.is_limit_up or snapshot.is_limit_down or not not_extended
        reason = (
            f"trend_quality={trend_quality:.2f}, r20={snapshot.return_20d:.2%}, "
            f"r60={snapshot.return_60d:.2%}"
        )
        return AgentSignal(self.name, snapshot.code, score, 0.80, risk_flag, reason)


