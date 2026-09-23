from __future__ import annotations

from dataclasses import asdict

from .accounting import end_trading_day, start_trading_day
from .models import AccountState, Decision, Order, StockSnapshot
from .pipeline import TradingPipeline


class PaperTradingSession:
    """Multi-day paper trading state machine.

    The session executes decisions produced on the previous trading day, then
    generates a new close-to-next-day plan. This keeps the MVP aligned with the
    A-share workflow: after-close planning and next-session execution.
    """

    def __init__(self, initial_cash: float, pipeline: TradingPipeline | None = None) -> None:
        self.account = AccountState(cash=initial_cash, peak_equity=initial_cash, previous_equity=initial_cash)
        self.pipeline = pipeline or TradingPipeline()
        self.pending_decisions: list[Decision] = []

    def run_day(self, snapshots: list[StockSnapshot]) -> dict[str, object]:
        if not snapshots:
            return {"error": "empty_snapshots"}

        trade_date = snapshots[0].trade_date
        snapshots_by_code = {snapshot.code: snapshot for snapshot in snapshots}

        start_trading_day(self.account, snapshots_by_code)
        executed_orders = self._build_orders_from_pending(snapshots, trade_date)
        fills = self.pipeline.broker.execute(executed_orders, snapshots_by_code, self.account)
        end_trading_day(self.account, snapshots_by_code)

        plan = self.pipeline.plan_day(snapshots, self.account)
        self.pending_decisions = [
            Decision(**decision)
            for decision in plan["decisions"]
            if decision["risk_check"] == "approved" and decision["action"] in {"buy", "sell"}
        ]

        return {
            "trade_date": str(trade_date),
            "executed_previous_plan": {
                "orders": [asdict(order) for order in executed_orders],
                "fills": [asdict(fill) for fill in fills],
            },
            "new_plan": plan,
            "pending_decision_count": len(self.pending_decisions),
        }

    def _build_orders_from_pending(self, snapshots: list[StockSnapshot], trade_date) -> list[Order]:
        snapshots_by_code = {snapshot.code: snapshot for snapshot in snapshots}
        checked: list[Decision] = []
        for decision in self.pending_decisions:
            snapshot = snapshots_by_code.get(decision.code)
            if not snapshot:
                continue
            checked.append(self.pipeline.risk_agent.check_decision(decision, snapshot, self.account))

        order_inputs = {
            decision.code: (decision.action, decision.target_weight, decision.reason)
            for decision in checked
            if decision.risk_check == "approved" and decision.action in {"buy", "sell"}
        }
        return self.pipeline.broker.build_orders(order_inputs, snapshots_by_code, self.account, planned_for=trade_date)
