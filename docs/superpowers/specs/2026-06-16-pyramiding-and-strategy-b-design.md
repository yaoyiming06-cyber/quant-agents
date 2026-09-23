# Pyramiding And Strategy B Design

## Goal

Add a controlled pyramiding mechanism to the main simulated strategy and create an independent Strategy B simulation that starts from the current main-strategy equity, uses lower buy thresholds, and is visible in a dedicated dashboard page.

## Strategy A Pyramiding

The main strategy keeps its current entry rules and adds only to winning positions. A position can receive an add-on buy when it remains in the preferred pool, is profitable, has enough rolling confirmations, has no danger-level anomaly, has no intraday health blocker, and is below the strategy target cap. The first add-on requires at least 3% unrealized profit; the second add-on requires at least 6% unrealized profit and continued score strength. Each add-on is small and capped so a single stock cannot exceed the dynamic single-stock cap or the configured pyramiding cap.

Loss-making positions are never averaged down. They continue through the existing stop-loss, sector-pressure, and exit-watch logic.

## Strategy B Shadow Simulation

Strategy B is a separate simulation ledger stored at `data/features/simulated_trading_b.json`. It starts on 2026-06-16 with the same equity level as the main strategy at the time of introduction and uses the same trading engine, costs, slippage, sell rules, T+1 rules, and pyramiding rules. Its only intentional difference is a lower buy threshold profile:

- Base `minBuyScore`: 0.765.
- Market-regime score adjustments are roughly half of Strategy A's adjustments.
- Position count, exposure caps, sell rules, and risk blockers remain the same.

This keeps B useful as a controlled experiment for "lower entry line" without turning it into a different strategy.

## T+1 And Position Lots

Add-on buys must not make today's new shares sellable. Positions will gain a `lots` list so the UI can show a combined position while the engine tracks which shares are sellable by trade date. Existing positions without lots are migrated into one legacy lot using their current `entryTradeDate`, shares, and cost basis.

Sells consume sellable lots first, and same-day add-on lots remain locked until the next trade date.

## Dashboard

The dashboard data bundle exposes:

- `simulatedTrading`: main Strategy A simulation.
- `simulatedTradingB`: Strategy B simulation.
- `strategySimulationComparison`: account and performance comparison between A and B.

The web dashboard gains a dedicated Strategy B page linked from the side navigation and the main trading page. The page presents B account metrics, positions, recent fills, pending signals, and a comparison card against Strategy A. The visual style may diverge from the existing page with a more premium, animated, glass-like presentation while retaining existing data loading patterns.

## Safety

The system remains simulation-only. Strategy B must not mutate Strategy A state. Pyramiding must respect quote freshness, execution windows, cash, total exposure limits, individual stock caps, T+1, danger anomalies, and sector pressure.
