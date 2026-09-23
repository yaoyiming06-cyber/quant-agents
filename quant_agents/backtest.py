from __future__ import annotations

from dataclasses import replace
from itertools import product
from math import sqrt
from statistics import mean, stdev

from .config import AgentWeights, AppConfig
from .models import StockSnapshot
from .paper import PaperTradingSession
from .pipeline import TradingPipeline


def run_backtest(history: list[list[StockSnapshot]], config: AppConfig | None = None, initial_cash: float = 1_000_000) -> dict[str, object]:
    session = PaperTradingSession(initial_cash=initial_cash, pipeline=TradingPipeline(config))
    reports: list[dict[str, object]] = []
    equity_curve: list[float] = []
    fill_count = 0
    rejected_count = 0

    for snapshots in history:
        report = session.run_day(snapshots)
        reports.append(report)
        account = report["new_plan"]["account"]
        equity_curve.append(float(account["equity"]))
        fills = report["executed_previous_plan"]["fills"]
        fill_count += sum(1 for fill in fills if fill["status"] == "filled")
        rejected_count += sum(1 for fill in fills if fill["status"] != "filled")

    metrics = compute_metrics(equity_curve, initial_cash)
    metrics["fill_count"] = fill_count
    metrics["rejected_count"] = rejected_count
    return {
        "metrics": metrics,
        "equity_curve": equity_curve,
        "last_report": reports[-1] if reports else {},
        "reports": reports,
    }


def compute_metrics(equity_curve: list[float], initial_cash: float) -> dict[str, float]:
    if not equity_curve:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "calmar": 0.0,
        }

    returns = []
    previous = initial_cash
    peak = initial_cash
    max_drawdown = 0.0
    for equity in equity_curve:
        returns.append(equity / previous - 1)
        previous = equity
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, 1 - equity / peak)

    total_return = equity_curve[-1] / initial_cash - 1
    annual_return = (1 + total_return) ** (252 / max(1, len(equity_curve))) - 1
    sharpe = _sharpe(returns)
    calmar = annual_return / max_drawdown if max_drawdown > 0 else max(0.0, annual_return)
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "calmar": calmar,
    }


def optimize_parameters(history: list[list[StockSnapshot]], base_config: AppConfig | None = None) -> list[dict[str, object]]:
    config = base_config or AppConfig()
    candidates = _candidate_configs(config)

    results: list[dict[str, object]] = []
    for candidate in candidates:
        backtest = run_backtest(history, candidate)
        metrics = backtest["metrics"]
        results.append(
            {
                "final_score": 0.0,
                "strategy": {
                    "max_positions": candidate.strategy.max_positions,
                    "min_final_score": candidate.strategy.min_final_score,
                    "stop_loss_pct": candidate.strategy.stop_loss_pct,
                    "max_single_weight": candidate.strategy.max_single_weight,
                    "base_total_weight": candidate.strategy.base_total_weight,
                },
                "weights": _weights_dict(candidate.weights),
                "metrics": metrics,
            }
        )

    _score_candidate_results(results)
    results.sort(key=lambda item: item["final_score"], reverse=True)
    return results


def rolling_optimize(
    history: list[list[StockSnapshot]],
    train_days: int = 4,
    validation_days: int = 2,
    step: int = 1,
) -> list[dict[str, object]]:
    windows: list[dict[str, object]] = []
    start = 0
    while start + train_days + validation_days <= len(history):
        train = history[start : start + train_days]
        validation = history[start + train_days : start + train_days + validation_days]
        best = optimize_parameters(train)[0]
        config = _config_from_candidate(best)
        validation_result = run_backtest(validation, config)
        windows.append(
            {
                "window_start": str(train[0][0].trade_date),
                "window_end": str(validation[-1][0].trade_date),
                "selected_strategy": best["strategy"],
                "selected_weights": best.get("weights", {}),
                "train_metrics": best["metrics"],
                "validation_metrics": validation_result["metrics"],
            }
        )
        start += step
    return windows


def optimize_cycle(
    history: list[list[StockSnapshot]],
    train_days: int = 60,
    validation_days: int = 20,
    test_days: int = 5,
    step: int = 5,
    top_train_candidates: int = 12,
    base_config: AppConfig | None = None,
) -> list[dict[str, object]]:
    windows: list[dict[str, object]] = []
    start = 0
    while start + train_days + validation_days <= len(history):
        train = history[start : start + train_days]
        validation = history[start + train_days : start + train_days + validation_days]
        test = history[start + train_days + validation_days : start + train_days + validation_days + test_days]
        train_candidates = optimize_parameters(train, base_config)[:top_train_candidates]

        validation_rows: list[dict[str, object]] = []
        for candidate in train_candidates:
            config = _config_from_candidate(candidate)
            validation_result = run_backtest(validation, config)
            validation_score = _absolute_metrics_score(validation_result["metrics"])
            train_score = _absolute_metrics_score(candidate["metrics"])
            stability_penalty = max(0.0, train_score - validation_score) * 0.35
            selection_score = 0.65 * validation_score + 0.35 * train_score - stability_penalty
            validation_rows.append(
                {
                    "selection_score": selection_score,
                    "train_score": train_score,
                    "validation_score": validation_score,
                    "strategy": candidate["strategy"],
                    "weights": candidate.get("weights", {}),
                    "train_metrics": candidate["metrics"],
                    "validation_metrics": validation_result["metrics"],
                }
            )

        validation_rows.sort(key=lambda item: item["selection_score"], reverse=True)
        selected = validation_rows[0] if validation_rows else {}
        test_metrics = {}
        if selected and test:
            test_metrics = run_backtest(test, _config_from_candidate(selected))["metrics"]
        windows.append(
            {
                "window_start": str(train[0][0].trade_date),
                "train_end": str(train[-1][0].trade_date),
                "validation_end": str(validation[-1][0].trade_date),
                "test_end": str(test[-1][0].trade_date) if test else None,
                "selected": selected,
                "test_metrics": test_metrics,
            }
        )
        start += step
    return windows


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    volatility = stdev(returns)
    if volatility == 0:
        return 0.0
    return mean(returns) / volatility * sqrt(252)


def _candidate_configs(config: AppConfig) -> list[AppConfig]:
    candidates: list[AppConfig] = []
    for max_positions, min_score, stop_loss, max_single_weight, base_total_weight, weights in product(
        (6, 8, 10),
        (0.52, 0.56, 0.60),
        (0.06, 0.08, 0.10),
        (0.10, 0.12),
        (0.60, 0.75),
        _weight_profiles(),
    ):
        strategy = replace(
            config.strategy,
            max_positions=max_positions,
            min_final_score=min_score,
            stop_loss_pct=stop_loss,
            max_single_weight=max_single_weight,
            base_total_weight=base_total_weight,
        )
        candidates.append(replace(config, strategy=strategy, weights=weights))
    return candidates


def _score_candidate_results(results: list[dict[str, object]]) -> None:
    if not results:
        return
    calmar = _rank_map(results, "calmar", higher_is_better=True)
    sharpe = _rank_map(results, "sharpe", higher_is_better=True)
    annual = _rank_map(results, "annual_return", higher_is_better=True)
    drawdown = _rank_map(results, "max_drawdown", higher_is_better=False)
    rejected = _rank_map(results, "rejected_count", higher_is_better=False)
    for index, item in enumerate(results):
        item["final_score"] = (
            0.34 * calmar[index]
            + 0.24 * sharpe[index]
            + 0.18 * annual[index]
            + 0.18 * drawdown[index]
            + 0.06 * rejected[index]
        )


def _rank_map(results: list[dict[str, object]], key: str, higher_is_better: bool) -> dict[int, float]:
    values = [(index, float(item["metrics"].get(key, 0.0))) for index, item in enumerate(results)]
    values.sort(key=lambda item: item[1], reverse=higher_is_better)
    if len(values) == 1:
        return {values[0][0]: 1.0}
    denominator = len(values) - 1
    return {index: 1 - rank / denominator for rank, (index, _) in enumerate(values)}


def _absolute_metrics_score(metrics: dict[str, float]) -> float:
    return (
        0.34 * _clip_unit(metrics.get("calmar", 0.0) / 3.0)
        + 0.24 * _clip_unit(metrics.get("sharpe", 0.0) / 3.0)
        + 0.18 * _clip_unit(metrics.get("annual_return", 0.0))
        + 0.18 * (1 - _clip_unit(metrics.get("max_drawdown", 0.0) / 0.20))
        + 0.06 * (1 - _clip_unit(metrics.get("rejected_count", 0.0) / 20.0))
    )


def _clip_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _weight_profiles() -> tuple[AgentWeights, ...]:
    return (
        AgentWeights(),
        AgentWeights(trend=0.34, capital=0.18, information=0.12, fundamental=0.14, liquidity=0.05, sentiment=0.12, institutional=0.05),
        AgentWeights(trend=0.42, capital=0.20, information=0.08, fundamental=0.12, liquidity=0.05, sentiment=0.08, institutional=0.05),
        AgentWeights(trend=0.32, capital=0.16, information=0.12, fundamental=0.22, liquidity=0.04, sentiment=0.08, institutional=0.06),
    )


def _weights_dict(weights: AgentWeights) -> dict[str, float]:
    return {
        "trend": weights.trend,
        "capital": weights.capital,
        "information": weights.information,
        "fundamental": weights.fundamental,
        "liquidity": weights.liquidity,
        "sentiment": weights.sentiment,
        "institutional": weights.institutional,
    }


def _config_from_candidate(candidate: dict[str, object]) -> AppConfig:
    config = AppConfig()
    strategy_values = candidate["strategy"]
    weights_values = candidate.get("weights", {})
    return replace(
        config,
        strategy=replace(
            config.strategy,
            max_positions=int(strategy_values["max_positions"]),
            min_final_score=float(strategy_values["min_final_score"]),
            stop_loss_pct=float(strategy_values["stop_loss_pct"]),
            max_single_weight=float(strategy_values["max_single_weight"]),
            base_total_weight=float(strategy_values.get("base_total_weight", config.strategy.base_total_weight)),
        ),
        weights=AgentWeights(**weights_values) if weights_values else config.weights,
    )
