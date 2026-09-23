from __future__ import annotations

from .models import AccountState, Fill, Order, Position, StockSnapshot
from .utils import round_lot


class PaperBroker:
    def __init__(
        self,
        commission_rate: float = 0.00025,
        stamp_tax: float = 0.0005,
        slippage: float = 0.001,
        cash_buffer: float = 0.05,
    ) -> None:
        self.commission_rate = commission_rate
        self.stamp_tax = stamp_tax
        self.slippage = slippage
        self.cash_buffer = cash_buffer

    def build_orders(
        self,
        decisions_by_code: dict[str, tuple[str, float, str]],
        snapshots_by_code: dict[str, StockSnapshot],
        account: AccountState,
        planned_for=None,
    ) -> list[Order]:
        equity = account.equity()
        orders: list[Order] = []
        for code, (action, target_weight, reason) in decisions_by_code.items():
            snapshot = snapshots_by_code.get(code)
            if not snapshot:
                continue
            reference_price = _reference_price(snapshot)
            current_value = account.positions.get(code).market_value if code in account.positions else 0.0
            target_value = equity * target_weight
            diff_value = target_value - current_value
            if action == "sell":
                position = account.positions.get(code)
                if position and position.can_sell:
                    shares = round_lot(position.shares)
                    if shares > 0:
                        orders.append(Order(code, "sell", shares, reference_price, reason, planned_for))
            elif action == "buy" and diff_value > reference_price * 100:
                spendable_cash = max(0.0, account.cash - equity * self.cash_buffer)
                shares = round_lot(min(diff_value, spendable_cash) / reference_price)
                if shares > 0:
                    orders.append(Order(code, "buy", shares, reference_price, reason, planned_for))
        return orders

    def execute(self, orders: list[Order], snapshots_by_code: dict[str, StockSnapshot], account: AccountState) -> list[Fill]:
        fills: list[Fill] = []
        for order in orders:
            snapshot = snapshots_by_code[order.code]
            if order.side == "buy" and snapshot.is_limit_up:
                fills.append(Fill(order.code, order.side, 0, 0.0, 0.0, "rejected", "limit_up"))
                continue
            if order.side == "sell" and snapshot.is_limit_down:
                fills.append(Fill(order.code, order.side, 0, 0.0, 0.0, "rejected", "limit_down"))
                continue

            price = _fill_price(snapshot, order.side, self.slippage)
            gross = order.shares * price
            fee = max(5.0, gross * self.commission_rate)
            if order.side == "sell":
                fee += gross * self.stamp_tax

            if order.side == "buy":
                total_cost = gross + fee
                if total_cost > account.cash:
                    fills.append(Fill(order.code, order.side, 0, 0.0, 0.0, "rejected", "cash_not_enough"))
                    continue
                account.cash -= total_cost
                old = account.positions.get(order.code)
                if old:
                    new_shares = old.shares + order.shares
                    new_cost = (old.avg_cost * old.shares + gross) / new_shares
                    account.positions[order.code] = Position(order.code, new_shares, new_cost, price, 0, False)
                else:
                    account.positions[order.code] = Position(order.code, order.shares, price, price, 0, False)
            else:
                old = account.positions.get(order.code)
                if not old:
                    fills.append(Fill(order.code, order.side, 0, 0.0, 0.0, "rejected", "no_position"))
                    continue
                sell_shares = min(order.shares, old.shares)
                account.cash += sell_shares * price - fee
                remaining = old.shares - sell_shares
                if remaining > 0:
                    account.positions[order.code] = Position(order.code, remaining, old.avg_cost, price, old.holding_days, True)
                else:
                    account.positions.pop(order.code)

            fills.append(Fill(order.code, order.side, sell_shares if order.side == "sell" else order.shares, price, fee, "filled", order.reason))
        return fills


def _reference_price(snapshot: StockSnapshot) -> float:
    return snapshot.open_price if snapshot.open_price is not None else snapshot.close


def _fill_price(snapshot: StockSnapshot, side: str, slippage: float) -> float:
    price = _reference_price(snapshot)
    return price * (1 + slippage if side == "buy" else 1 - slippage)
