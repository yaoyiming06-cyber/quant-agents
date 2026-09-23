from __future__ import annotations

from ..config import RiskConfig, StrategyConfig
from ..models import AccountState, Decision, StockSnapshot


class RiskAgent:
    name = "risk"
    hard_veto_prefixes = ("information:", "fundamental:")

    def __init__(self, risk_config: RiskConfig, strategy_config: StrategyConfig) -> None:
        self.risk_config = risk_config
        self.strategy_config = strategy_config

    def check_account(self, account: AccountState) -> tuple[bool, str]:
        if account.drawdown() >= self.risk_config.max_account_drawdown:
            return False, f"account_drawdown_exceeded:{account.drawdown():.2%}"
        if account.daily_pnl_pct() <= -self.risk_config.max_daily_loss:
            return False, f"daily_loss_exceeded:{account.daily_pnl_pct():.2%}"
        if len(account.positions) > self.risk_config.max_position_count:
            return False, "position_count_exceeded"
        return True, "account_ok"

    def check_decision(
        self,
        decision: Decision,
        snapshot: StockSnapshot,
        account: AccountState,
    ) -> Decision:
        account_ok, account_reason = self.check_account(account)
        if not account_ok and decision.action == "buy":
            return _reject(decision, account_reason)
        if self.risk_config.block_if_data_stale and snapshot.is_data_stale:
            return _reject(decision, "data_stale")
        if snapshot.is_suspended:
            return _reject(decision, "suspended")
        if snapshot.is_st:
            return _reject(decision, "st_stock")
        if decision.action == "buy" and _has_hard_veto(decision):
            return _reject(decision, "agent_hard_veto")
        if decision.action == "buy" and _has_negative_event(snapshot):
            return _reject(decision, "negative_information_event")
        if decision.action == "buy" and snapshot.is_limit_up:
            return _reject(decision, "limit_up_not_buyable")
        if decision.action == "sell" and snapshot.is_limit_down:
            return _reject(decision, "limit_down_not_sellable")
        if decision.target_weight > self.strategy_config.max_single_weight:
            return _reject(decision, "single_weight_exceeded")
        return Decision(
            code=decision.code,
            action=decision.action,
            final_score=decision.final_score,
            target_weight=decision.target_weight,
            reason=decision.reason,
            risk_check="approved",
            risk_flags=decision.risk_flags,
        )


def _reject(decision: Decision, reason: str) -> Decision:
    return Decision(
        code=decision.code,
        action="hold",
        final_score=decision.final_score,
        target_weight=0.0,
        reason=decision.reason,
        risk_check=f"rejected:{reason}",
        risk_flags=decision.risk_flags,
    )


def _has_hard_veto(decision: Decision) -> bool:
    return any(flag.startswith(RiskAgent.hard_veto_prefixes) for flag in decision.risk_flags)


def _has_negative_event(snapshot: StockSnapshot) -> bool:
    if snapshot.news_evidence:
        return bool(snapshot.news_evidence.get("material_risk")) or snapshot.news_evidence.get("severity") == "high"
    if not snapshot.info_risk:
        return False
    text = snapshot.info_risk.lower()
    return any(
        keyword in text
        for keyword in (
            "investigation",
            "penalty",
            "delist",
            "fraud",
            "major_reduction",
            "立案调查",
            "行政处罚",
            "退市",
            "重大风险",
        )
    )
