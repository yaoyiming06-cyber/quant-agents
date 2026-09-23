from __future__ import annotations

from dataclasses import asdict

from .accounting import snapshot_position
from .agents.capital import CapitalAgent
from .agents.fundamental import FundamentalAgent
from .agents.information import InformationAgent
from .agents.institutional import InstitutionalFlowAgent
from .agents.risk import RiskAgent
from .agents.sentiment import SentimentAgent
from .agents.trend import TrendAgent
from .broker import PaperBroker
from .config import AppConfig
from .decision import DecisionEngine
from .models import AccountState, AgentSignal, Decision, StockSnapshot
from .universe import filter_universe


class TradingPipeline:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig()
        self.agents = [
            TrendAgent(),
            CapitalAgent(),
            InformationAgent(),
            FundamentalAgent(),
            InstitutionalFlowAgent(),
            SentimentAgent(),
        ]
        self.decision_engine = DecisionEngine(self.config.weights, self.config.strategy)
        self.risk_agent = RiskAgent(self.config.risk, self.config.strategy)
        self.broker = PaperBroker(cash_buffer=self.config.risk.min_cash_buffer)

    def plan_day(self, snapshots: list[StockSnapshot], account: AccountState) -> dict[str, object]:
        universe = filter_universe(snapshots, self.config.universe)
        signals: list[AgentSignal] = []
        for snapshot in universe:
            for agent in self.agents:
                signals.append(agent.score(snapshot))

        snapshots_by_code = {snapshot.code: snapshot for snapshot in snapshots}
        trade_date = snapshots[0].trade_date if snapshots else None
        raw_decisions = self.decision_engine.decide(
            signals,
            account.positions,
            trade_date=trade_date,
            snapshots_by_code=snapshots_by_code,
        )
        decisions: list[Decision] = []
        for decision in raw_decisions:
            snapshot = snapshots_by_code.get(decision.code)
            if not snapshot:
                continue
            decisions.append(self.risk_agent.check_decision(decision, snapshot, account))

        return {
            "trade_date": str(trade_date) if trade_date else None,
            "universe": [snapshot.code for snapshot in universe],
            "signals": [_serialize_signal(signal) for signal in signals],
            "decisions": [asdict(decision) for decision in decisions],
            "account": _serialize_account(account),
        }

    def run_once(self, snapshots: list[StockSnapshot], account: AccountState) -> dict[str, object]:
        plan = self.plan_day(snapshots, account)
        snapshots_by_code = {snapshot.code: snapshot for snapshot in snapshots}
        order_inputs = {
            decision["code"]: (decision["action"], decision["target_weight"], decision["reason"])
            for decision in plan["decisions"]
            if decision["risk_check"] == "approved" and decision["action"] in {"buy", "sell"}
        }
        orders = self.broker.build_orders(order_inputs, snapshots_by_code, account)
        fills = self.broker.execute(orders, snapshots_by_code, account)

        return {
            **plan,
            "orders": [asdict(order) for order in orders],
            "fills": [asdict(fill) for fill in fills],
            "account": _serialize_account(account),
        }


def _serialize_signal(signal: AgentSignal) -> dict[str, object]:
    return asdict(signal)


def _serialize_account(account: AccountState) -> dict[str, object]:
    return {
        "cash": account.cash,
        "equity": account.equity(),
        "drawdown": account.drawdown(),
        "daily_pnl_pct": account.daily_pnl_pct(),
        "positions": {code: snapshot_position(position) for code, position in account.positions.items()},
    }
