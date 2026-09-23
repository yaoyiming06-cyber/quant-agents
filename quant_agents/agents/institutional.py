from __future__ import annotations

from ..models import AgentSignal, StockSnapshot
from ..utils import clamp


class InstitutionalFlowAgent:
    """Heuristic score for institutional/quant participation traces.

    This does not claim that a named institution has bought the stock. It only
    scores public-market footprints that often accompany institutional or
    systematic participation: large liquidity, active turnover, trend factor
    exposure, and abnormal momentum.
    """

    name = "institutional"

    def score(self, snapshot: StockSnapshot) -> AgentSignal:
        heuristic_score, crowding_risk, heuristic_evidence = _heuristic_score(snapshot)
        external_score, external_confidence, external_risk, external_evidence = _external_evidence_score(
            snapshot.institutional_evidence
        )
        if external_score is None:
            score = heuristic_score
            confidence = 0.50
        else:
            score = clamp(0.60 * heuristic_score + 0.40 * external_score)
            confidence = max(0.50, external_confidence)
        risk_flag = crowding_risk or external_risk
        evidence = heuristic_evidence + external_evidence
        if risk_flag:
            evidence.append("participation_or_crowding_risk=true")
        return AgentSignal(
            self.name,
            snapshot.code,
            score,
            confidence,
            risk_flag,
            ", ".join(evidence),
            {
                "interpretation": "participation_trace_only",
                "not_named_institution_buy": True,
                "crowding_risk": crowding_risk,
                "has_external_evidence": external_score is not None,
                "external_evidence": snapshot.institutional_evidence,
            },
        )


def _heuristic_score(snapshot: StockSnapshot) -> tuple[float, bool, list[str]]:
        liquidity_score = clamp((snapshot.avg_turnover_20d - 500_000_000) / 9_500_000_000)
        turnover_score = clamp(snapshot.turnover_rate_20d / 0.06) if snapshot.turnover_rate_20d > 0 else 0.45
        trend_factor = 1.0 if snapshot.close > snapshot.ma20 > snapshot.ma60 else 0.35
        momentum_score = clamp((snapshot.return_20d + 0.02) / 0.35)
        crowding_risk = snapshot.return_20d > 0.45 or snapshot.turnover_rate_20d > 0.15
        crowding_penalty = 0.15 if crowding_risk else 0.0
        score = clamp(
            0.35 * liquidity_score
            + 0.20 * turnover_score
            + 0.25 * trend_factor
            + 0.20 * momentum_score
            - crowding_penalty
        )
        evidence = [
            f"avg_turnover={snapshot.avg_turnover_20d:.0f}",
            f"turnover_rate={snapshot.turnover_rate_20d:.2%}",
            f"r20={snapshot.return_20d:.2%}",
            f"factor_trend={trend_factor:.2f}",
        ]
        if crowding_risk:
            evidence.append("crowding_risk=true")
        return score, crowding_risk, evidence


def _external_evidence_score(evidence: dict[str, object]) -> tuple[float | None, float, bool, list[str]]:
    if not evidence:
        return None, 0.50, False, []

    points = 0.0
    confidence = 0.60
    risk = False
    reasons: list[str] = []

    dragon = _dict(evidence.get("dragon_tiger"))
    institution_net_buy = _float(dragon.get("institution_net_buy"))
    institution_seat_count = _float(dragon.get("institution_seat_count"))
    if institution_net_buy > 0:
        points += 0.25
        confidence = max(confidence, 0.75)
        reasons.append(f"dragon_tiger_institution_net_buy={institution_net_buy:.0f}")
    elif institution_net_buy < 0:
        points -= 0.20
        confidence = max(confidence, 0.75)
        reasons.append(f"dragon_tiger_institution_net_sell={institution_net_buy:.0f}")
    if institution_seat_count > 0:
        points += min(0.12, institution_seat_count * 0.04)
        reasons.append(f"institution_seats={institution_seat_count:.0f}")
    seat_quality = _dict(dragon.get("seat_quality"))
    seat_adjustment = _float(seat_quality.get("score_adjustment"))
    if seat_adjustment:
        points += seat_adjustment
        if seat_adjustment < 0:
            risk = True
        reasons.append(f"dragon_tiger_seat_quality={seat_adjustment:.3f}")

    northbound = _dict(evidence.get("northbound"))
    north_change_pct = _float(northbound.get("holding_change_pct"))
    north_change_shares = _float(northbound.get("holding_change_shares"))
    if north_change_pct > 0 or north_change_shares > 0:
        points += 0.16
        confidence = max(confidence, 0.70)
        reasons.append(f"northbound_increase_pct={north_change_pct:.4f}")
    elif north_change_pct < 0 or north_change_shares < 0:
        points -= 0.10
        reasons.append(f"northbound_decrease_pct={north_change_pct:.4f}")

    margin = _dict(evidence.get("margin"))
    financing_balance_change = _float(margin.get("financing_balance_change"))
    if financing_balance_change > 0:
        points += 0.08
        reasons.append(f"financing_balance_increase={financing_balance_change:.0f}")
    elif financing_balance_change < 0:
        points -= 0.05
        reasons.append(f"financing_balance_decrease={financing_balance_change:.0f}")

    fund = _dict(evidence.get("fund"))
    fund_holding_change = _float(fund.get("fund_holding_change"))
    fund_count = _float(fund.get("fund_count"))
    if fund_holding_change > 0 or fund_count > 0:
        points += min(0.16, 0.08 + fund_count * 0.01)
        reasons.append(f"fund_holding_change={fund_holding_change:.4f}")
    elif fund_holding_change < 0:
        points -= 0.08
        reasons.append(f"fund_holding_reduction={fund_holding_change:.4f}")

    manual = _dict(evidence.get("manual"))
    manual_adjustment = _float(manual.get("score_adjustment"))
    if manual_adjustment:
        points += manual_adjustment
        reasons.append(f"manual_adjustment={manual_adjustment:.3f}")

    crowding = _dict(evidence.get("crowding"))
    crowding_score = _float(crowding.get("score"))
    if crowding_score >= 0.8:
        points -= 0.12
        risk = True
        reasons.append(f"external_crowding={crowding_score:.2f}")

    score = clamp(0.50 + points)
    return score, confidence, risk, reasons


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
