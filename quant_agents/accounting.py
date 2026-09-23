from __future__ import annotations

from dataclasses import replace

from .models import AccountState, Position, StockSnapshot


def start_trading_day(account: AccountState, snapshots_by_code: dict[str, StockSnapshot]) -> None:
    account.previous_equity = account.equity()
    for code, position in list(account.positions.items()):
        snapshot = snapshots_by_code.get(code)
        price = _open_or_close(snapshot) if snapshot else position.market_price
        account.positions[code] = replace(
            position,
            market_price=price,
            holding_days=position.holding_days + 1,
            can_sell=True,
        )


def end_trading_day(account: AccountState, snapshots_by_code: dict[str, StockSnapshot]) -> None:
    for code, position in list(account.positions.items()):
        snapshot = snapshots_by_code.get(code)
        if not snapshot:
            continue
        account.positions[code] = replace(position, market_price=snapshot.close)
    equity = account.equity()
    account.peak_equity = max(account.peak_equity or equity, equity)


def snapshot_position(position: Position) -> dict[str, object]:
    return {
        "code": position.code,
        "shares": position.shares,
        "avg_cost": position.avg_cost,
        "market_price": position.market_price,
        "market_value": position.market_value,
        "pnl_pct": position.pnl_pct,
        "holding_days": position.holding_days,
        "can_sell": position.can_sell,
    }


def _open_or_close(snapshot: StockSnapshot | None) -> float:
    if not snapshot:
        return 0.0
    return snapshot.open_price if snapshot.open_price is not None else snapshot.close

