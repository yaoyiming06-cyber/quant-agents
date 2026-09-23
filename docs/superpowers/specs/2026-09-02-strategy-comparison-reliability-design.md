# Strategy Comparison Reliability Design

## Scope

Keep the A/B/C candidate strategy comparison. Remove the independent B
simulation account and replace it with an A2 comparison account. A2 uses the
same candidate pool and execution engine as A, but disables `mainline_probe`.

## Historical Candidate Returns

Each candidate record will preserve its selection date and selection price.
When a future trading-day close becomes available, the system will write the
return for that fixed holding period once:

- `return1d`
- `return3d`
- `return5d`
- `return10d`
- `return20d`

The dashboard and aggregate A/B/C statistics will use these fixed-period
returns, not a historical row's changing latest price. The existing
latest-price movement remains available only as a current-status field where
needed, and is not used for historical strategy evaluation.

The 2026-06-04 quote-date mismatch remains a data-quality alert. This change
does not rewrite its history or infer a replacement value.

## Simulation Accounts

The current A simulation remains the primary account.

The independent B simulation account, its state file, its builder functions,
and its dashboard route and panels will be removed. B remains a candidate
strategy in `strategy_comparison.json`; this does not remove B candidates or
their A/B/C comparisons.

A new `simulated_trading_a2.json` account will start from the primary A
account's equity once, then retain its original `startDate` and initial cash
on subsequent dashboard builds. This prevents refreshes from relabeling the
account as having started on the latest data date.

A2 keeps A's execution assumptions: fees, slippage, T+1, stop rules, market
regime guards, and position limits. Its only strategy change is
`mainline_probe` disabled. This creates a narrow, explainable control
experiment for measuring that entry channel.

## Dashboard

Dashboard payload fields become `simulatedTradingA2` and
`strategySimulationComparison`. The comparison names the accounts A and A2
and shows return, equity, fees and taxes, slippage, completed-trade win rate,
profit factor, trade count, drawdown, and current exposure.

The former B simulation page, navigation, cards, chart markers, and all
references to `simulatedTradingB` are removed. Existing A/B/C candidate
comparison presentation remains unchanged except that it reads fixed-period
candidate returns.

## Validation

Tests will prove that:

1. A historical selection receives each target-period return once and later
   dashboard refreshes do not change it.
2. A2 initializes only once and retains its original `startDate`.
3. A2 configuration disables `mainline_probe` without changing A's risk and
   execution assumptions.
4. The dashboard data emits A and A2, while B candidate comparison data stays
   present.
5. The front end has no B simulation navigation or `simulatedTradingB`
   dependency.
