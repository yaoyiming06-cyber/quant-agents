# Interface Checklist

## Market Data

- `code`
- `trade_date`
- `open`
- `high`
- `low`
- `close`
- `prev_close`
- `volume`
- `amount`
- `turnover_rate`
- `limit_up_price`
- `limit_down_price`
- `is_suspended`
- `is_st`
- `listing_days`
- `adjust_factor`
- `data_timestamp`
- `is_data_stale`

## Account

- `total_asset`
- `cash`
- `available_cash`
- `frozen_cash`
- `market_value`
- `daily_pnl`
- `drawdown`
- `previous_equity`
- `peak_equity`

## Position

- `code`
- `shares`
- `available_shares`
- `avg_cost`
- `market_price`
- `market_value`
- `pnl`
- `holding_days`
- `can_sell`
- `available_shares`

## Order

- `order_id`
- `code`
- `side`
- `price`
- `quantity`
- `filled_quantity`
- `filled_avg_price`
- `status`
- `reject_reason`
- `created_at`
- `updated_at`
- `source_plan_date`

## Decision

- `trade_date`
- `code`
- `action`
- `target_weight`
- `final_score`
- `risk_flags`
- `risk_check`
- `reason`

## Alerts

- Market data stale.
- Missing ST, suspension, or limit-price fields.
- Duplicate order.
- Order rejected.
- Fill price deviates from expected price.
- Drawdown limit breached.
- Strategy process failed.
- Account and local position mismatch.
