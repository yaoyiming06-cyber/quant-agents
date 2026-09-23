from __future__ import annotations

from collections import defaultdict
from datetime import date

from .config import AgentWeights, StrategyConfig
from .models import AgentSignal, Decision, Position, StockSnapshot
from .utils import clamp


class DecisionEngine:
    def __init__(self, weights: AgentWeights, strategy: StrategyConfig) -> None:
        self.weights = weights
        self.strategy = strategy

    def decide(
        self,
        signals: list[AgentSignal],
        current_positions: dict[str, Position],
        trade_date: date | None = None,
        snapshots_by_code: dict[str, StockSnapshot] | None = None,
    ) -> list[Decision]:
        grouped: dict[str, dict[str, AgentSignal]] = defaultdict(dict)
        for signal in signals:
            grouped[signal.code][signal.agent_name] = signal

        scored: list[tuple[str, float, str, tuple[str, ...]]] = []
        for code, by_agent in grouped.items():
            risk_penalty = sum(_risk_penalty(signal) for signal in by_agent.values() if signal.risk_flag)
            score = (
                self.weights.trend * _score(by_agent, "trend")
                + self.weights.capital * _score(by_agent, "capital")
                + self.weights.information * _score(by_agent, "information")
                + self.weights.fundamental * _score(by_agent, "fundamental")
                + self.weights.sentiment * _score(by_agent, "sentiment")
                + self.weights.liquidity * _score(by_agent, "capital")
                + self.weights.institutional * _score(by_agent, "institutional")
                - risk_penalty
            )
            reasons = "; ".join(f"{name}:{signal.reason}" for name, signal in sorted(by_agent.items()))
            risk_flags = tuple(
                f"{name}:{signal.reason}" for name, signal in sorted(by_agent.items()) if signal.risk_flag
            )
            scored.append((code, clamp(score), reasons, risk_flags))

        scored.sort(key=lambda item: item[1], reverse=True)
        should_rebalance = trade_date is None or trade_date.weekday() in self.strategy.rebalance_weekdays
        selected = (
            [item for item in scored if item[1] >= self.strategy.min_final_score][: self.strategy.max_positions]
            if should_rebalance
            else []
        )
        selected_codes = {code for code, _, _, _ in selected}
        target_weights = _allocate_target_weights(selected, self.strategy)

        decisions: list[Decision] = []
        for code, score, reasons, risk_flags in selected:
            snapshot = snapshots_by_code.get(code) if snapshots_by_code else None
            position = current_positions.get(code)
            if position and _exit_reason(position, snapshot, self.strategy):
                continue
            action = "hold" if code in current_positions else "buy"
            decisions.append(Decision(code, action, score, target_weights.get(code, 0.0), reasons, risk_flags=risk_flags))

        for code, position in current_positions.items():
            snapshot = snapshots_by_code.get(code) if snapshots_by_code else None
            exit_reason = _exit_reason(position, snapshot, self.strategy)
            if exit_reason:
                decisions.append(
                    Decision(
                        code=code,
                        action="sell",
                        final_score=0.0,
                        target_weight=0.0,
                        reason=exit_reason,
                    )
                )
                continue
            if code not in selected_codes:
                if not should_rebalance:
                    continue
                decisions.append(
                    Decision(
                        code=code,
                        action="sell",
                        final_score=0.0,
                        target_weight=0.0,
                        reason=f"not_selected; holding_days={position.holding_days}; pnl={position.pnl_pct:.2%}",
                    )
                )
        return decisions


def _score(by_agent: dict[str, AgentSignal], name: str) -> float:
    signal = by_agent.get(name)
    if not signal:
        return 0.50
    confidence = clamp(signal.confidence)
    return clamp(0.50 + (signal.score - 0.50) * (0.50 + 0.50 * confidence))


def _risk_penalty(signal: AgentSignal) -> float:
    base = {
        "information": 0.18,
        "fundamental": 0.12,
        "trend": 0.08,
        "capital": 0.08,
        "institutional": 0.08,
        "sentiment": 0.06,
    }.get(signal.agent_name, 0.06)
    return base * (0.50 + 0.50 * clamp(signal.confidence))


def _allocate_target_weights(
    selected: list[tuple[str, float, str, tuple[str, ...]]],
    strategy: StrategyConfig,
) -> dict[str, float]:
    if not selected:
        return {}
    total_budget = min(strategy.base_total_weight, strategy.max_single_weight * len(selected))
    raw = {code: max(0.01, score - strategy.min_final_score + 0.05) for code, score, _, _ in selected}
    remaining = set(raw)
    weights = {code: 0.0 for code in raw}
    remaining_budget = total_budget

    while remaining and remaining_budget > 0:
        raw_sum = sum(raw[code] for code in remaining)
        if raw_sum <= 0:
            equal = remaining_budget / len(remaining)
            for code in list(remaining):
                weights[code] += min(strategy.max_single_weight - weights[code], equal)
            break
        capped: set[str] = set()
        for code in list(remaining):
            add = remaining_budget * raw[code] / raw_sum
            allowed = strategy.max_single_weight - weights[code]
            if add >= allowed:
                weights[code] += allowed
                capped.add(code)
            else:
                weights[code] += add
        if not capped:
            break
        remaining -= capped
        remaining_budget = total_budget - sum(weights.values())

    return {code: clamp(weight, 0.0, strategy.max_single_weight) for code, weight in weights.items()}


def _exit_reason(position: Position, snapshot: StockSnapshot | None, strategy: StrategyConfig) -> str | None:
    if position.pnl_pct <= -strategy.stop_loss_pct:
        return f"stop_loss; pnl={position.pnl_pct:.2%}"
    if position.holding_days >= strategy.max_position_days:
        return f"max_position_days; holding_days={position.holding_days}"
    if snapshot and snapshot.close < snapshot.ma20:
        return f"trend_exit; close={snapshot.close:.2f}; ma20={snapshot.ma20:.2f}"
    return None
