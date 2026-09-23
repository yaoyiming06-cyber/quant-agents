from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UniverseConfig:
    include_prefixes: tuple[str, ...] = (
        "600",
        "601",
        "603",
        "605",
        "000",
        "001",
        "002",
        "003",
    )
    exclude_prefixes: tuple[str, ...] = ("300", "301", "688", "689", "8", "4")
    min_listing_days: int = 60
    min_avg_turnover: float = 100_000_000
    min_price: float = 3.0


@dataclass(frozen=True)
class StrategyConfig:
    max_positions: int = 8
    min_final_score: float = 0.55
    rebalance_weekdays: tuple[int, ...] = (0, 3)
    max_single_weight: float = 0.12
    base_total_weight: float = 0.75
    stop_loss_pct: float = 0.08
    max_position_days: int = 20


@dataclass(frozen=True)
class RiskConfig:
    max_account_drawdown: float = 0.10
    max_daily_loss: float = 0.025
    max_weekly_loss: float = 0.06
    max_position_count: int = 10
    min_cash_buffer: float = 0.05
    block_if_data_stale: bool = True


@dataclass(frozen=True)
class AgentWeights:
    trend: float = 0.38
    capital: float = 0.18
    information: float = 0.10
    fundamental: float = 0.15
    liquidity: float = 0.05
    sentiment: float = 0.10
    institutional: float = 0.04


@dataclass(frozen=True)
class AppConfig:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    weights: AgentWeights = field(default_factory=AgentWeights)
