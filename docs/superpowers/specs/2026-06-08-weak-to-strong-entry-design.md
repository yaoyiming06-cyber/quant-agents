# Weak-To-Strong Entry Design

## Goal

Add an independent morning weak-to-strong entry path without changing the
existing afternoon trend-confirmation path.

## Signal

The system records one observation per fresh quote for every preferred-pool
candidate. A weak-to-strong setup needs an initial weak observation followed by
two consecutive improving observations. Each confirmation requires:

- price and daily percentage change improve from the prior observation;
- the stock outperforms the current index average;
- price recovers above the intraday average price or open price;
- volume ratio is at least healthy and does not materially deteriorate;
- sector breadth/return improves and sector flow is not cooling or outflow;
- no limit-down, severe-drop, sector-breakdown, suspension, risk, or danger
  blocker is present.

The first observation is a baseline. The earliest possible buy is therefore
10:05 after 09:50 and 10:05 confirmations.

## Execution And Risk

- Existing afternoon confirmed buys remain unchanged.
- Weak-to-strong buys use reason `weak_to_strong_confirmed`.
- The weak-to-strong channel uses a separate score gate and smaller regime
  position sizes.
- In `risk_off`, each weak-to-strong position targets 2.5% and total
  weak-to-strong exposure is capped at 10%.
- All buys still obey overall position count, overall market-regime exposure,
  cash, fresh-quote, continuous-trading-hours, and T+1 rules.
- A same-day reversal buy cannot be sold until the next trading day.

## State And UI

`simulated_trading.json` stores a `weakToStrongWatch` map with recent
observations, confirmation count, state, reasons, and blockers. Pending signals
include the channel status. Trading orders and fills show a readable
weak-to-strong reason.

## Verification

Tests cover successful 09:35/09:50/10:05 confirmation, risk-off sizing,
rejection of sector outflow/fake rebounds, no duplicate normal buy, and T+1
locking.
