from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class StockSnapshot:
    code: str
    name: str
    trade_date: date
    close: float
    prev_close: float
    ma20: float
    ma60: float
    ma60_slope: float
    return_20d: float
    return_60d: float
    volatility_20d: float
    avg_turnover_20d: float
    turnover_rate_20d: float
    relative_strength: float
    listing_days: int
    is_st: bool = False
    is_suspended: bool = False
    is_limit_up: bool = False
    is_limit_down: bool = False
    open_price: float | None = None
    limit_up_price: float | None = None
    limit_down_price: float | None = None
    is_data_stale: bool = False
    info_risk: str | None = None
    pe_rank: float = 0.5
    roe_rank: float = 0.5
    cashflow_rank: float = 0.5
    institutional_evidence: dict[str, Any] = field(default_factory=dict)
    quote_evidence: dict[str, Any] = field(default_factory=dict)
    theme_evidence: dict[str, Any] = field(default_factory=dict)
    news_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Position:
    code: str
    shares: int
    avg_cost: float
    market_price: float
    holding_days: int = 0
    can_sell: bool = True

    @property
    def market_value(self) -> float:
        return self.shares * self.market_price

    @property
    def pnl_pct(self) -> float:
        if self.avg_cost <= 0:
            return 0.0
        return self.market_price / self.avg_cost - 1


@dataclass
class AccountState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    peak_equity: float | None = None
    previous_equity: float | None = None

    def equity(self) -> float:
        return self.cash + sum(position.market_value for position in self.positions.values())

    def drawdown(self) -> float:
        equity = self.equity()
        peak = self.peak_equity or equity
        if peak <= 0:
            return 0.0
        return max(0.0, 1 - equity / peak)

    def daily_pnl_pct(self) -> float:
        if not self.previous_equity:
            return 0.0
        return self.equity() / self.previous_equity - 1


@dataclass(frozen=True)
class AgentSignal:
    agent_name: str
    code: str
    score: float
    confidence: float
    risk_flag: bool
    reason: str
    raw_features: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    code: str
    action: str
    final_score: float
    target_weight: float
    reason: str
    risk_check: str = "pending"
    risk_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Order:
    code: str
    side: str
    shares: int
    limit_price: float
    reason: str
    planned_for: date | None = None


@dataclass(frozen=True)
class Fill:
    code: str
    side: str
    shares: int
    price: float
    fee: float
    status: str
    reason: str
