# Deployment Plan

## Phase 0: Local MVP

- Run the built-in sample pipeline.
- Confirm non-GEM universe filtering.
- Confirm every agent emits structured score, confidence, risk flag, and reason.
- Confirm decision, risk, paper order, fill, and account reports are generated.
- Confirm `run-paper-sample` uses previous-day decisions and next-day execution.
- Confirm `backtest-sample` and `optimize-sample` produce metrics and parameter candidates.

## Phase 1: Data Connection

Required data:

- Daily OHLCV with forward-adjusted prices.
- 60-minute OHLCV for trend confirmation.
- ST status, suspension status, limit-up and limit-down prices.
- Listing date and board classification.
- Turnover, amount, volume, bid/ask if available.
- Announcement and news events for the information agent.
- Basic financial factors for the fundamental agent.

## Phase 2: Broker Simulation

Required interface fields:

- Account total equity, available cash, frozen cash.
- Position quantity, available-to-sell quantity, average cost, market value.
- Order id, status, requested quantity, filled quantity, average fill price.
- Reject reason, cancel status, timestamp.

Do not grant withdrawal permission to any automated trading key.

## Phase 3: Paper Trading

Daily schedule:

- 08:30 update announcements, events, and risk lists.
- 09:20 check connectivity, account, universe, and yesterday plan.
- 09:35 simulate or execute approved orders.
- 11:40 run midday risk check.
- 15:30 update market data and generate next-day plan.
- 17:00 human review.

Paper trading should run for at least 20 trading days, preferably 60.

Execution rule:

- Day T close: generate decisions for the next trading session.
- Day T+1 start: roll positions forward, unlock T+1 sellable shares, recheck risk.
- Day T+1 execution: simulate fills using open price when available.
- Day T+1 close: mark to market and generate a new plan.

Risk rules now implemented in the MVP:

- ST, suspension, stale data, limit-up buy, and limit-down sell checks.
- Information/fundamental hard veto on new buys.
- Account drawdown and daily loss halt.
- Single-stock weight cap and cash buffer.
- Stop loss, max holding days, and close-below-MA20 exits.
- Rebalance weekdays for new buys and normal rotation exits.

## Human Approval Points

- First deployment.
- Agent weight changes.
- Risk threshold changes.
- New data source or broker connection.
- Switch from paper trading to live trading.
- Any capital increase.
- Any risk halt or abnormal order event.
- Any information-agent major negative event.

## Notes

- Agents can score and explain, but cannot send orders.
- The decision engine combines scores deterministically.
- The risk agent has final veto power.
- Real trading requires broker-specific adapter implementation and dry-run tests.
