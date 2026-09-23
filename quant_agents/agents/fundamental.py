from __future__ import annotations

from ..models import AgentSignal, StockSnapshot
from ..utils import clamp


class FundamentalAgent:
    name = "fundamental"

    def score(self, snapshot: StockSnapshot) -> AgentSignal:
        quote = snapshot.quote_evidence or {}
        quote_pe = _float(quote.get("pe_dynamic"))
        quote_pb = _float(quote.get("pb"))
        market_cap_100m = _float(quote.get("total_market_cap_100m"))

        pe_score = snapshot.pe_rank
        if quote_pe > 0:
            pe_score = clamp((80.0 - quote_pe) / 70.0)
        pb_score = 0.5
        if quote_pb > 0:
            pb_score = clamp((20.0 - quote_pb) / 18.0)
        size_score = 0.5
        if market_cap_100m > 0:
            size_score = clamp((market_cap_100m - 50.0) / 450.0)

        quality = (
            0.36 * snapshot.roe_rank
            + 0.28 * snapshot.cashflow_rank
            + 0.22 * pe_score
            + 0.08 * pb_score
            + 0.06 * size_score
        )
        score = clamp(quality)
        risk_flag = snapshot.roe_rank < 0.05 or snapshot.cashflow_rank < 0.05 or quote_pe > 120 or quote_pb > 30
        reason = (
            f"roe_rank={snapshot.roe_rank:.2f}, cashflow_rank={snapshot.cashflow_rank:.2f}, "
            f"pe_score={pe_score:.2f}, quote_pe={quote_pe:.2f}, pb={quote_pb:.2f}, "
            f"market_cap_100m={market_cap_100m:.2f}"
        )
        confidence = 0.68 if quote else 0.55
        return AgentSignal(
            self.name,
            snapshot.code,
            score,
            confidence,
            risk_flag,
            reason,
            {
                "quote_pe": quote_pe,
                "quote_pb": quote_pb,
                "market_cap_100m": market_cap_100m,
                "pe_score": pe_score,
                "pb_score": pb_score,
                "size_score": size_score,
                "has_quote_evidence": bool(quote),
            },
        )


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

