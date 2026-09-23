from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from .models import StockSnapshot


def load_sample_snapshots(trade_date: date | None = None) -> list[StockSnapshot]:
    day = trade_date or date.today()
    return [
        StockSnapshot(
            code="600519",
            name="Kweichow Moutai",
            trade_date=day,
            open_price=1510.0,
            close=1520.0,
            prev_close=1508.0,
            ma20=1460.0,
            ma60=1385.0,
            ma60_slope=0.003,
            return_20d=0.08,
            return_60d=0.14,
            volatility_20d=0.018,
            avg_turnover_20d=4_800_000_000,
            turnover_rate_20d=0.011,
            relative_strength=0.72,
            listing_days=6000,
            pe_rank=0.55,
            roe_rank=0.92,
            cashflow_rank=0.89,
        ),
        StockSnapshot(
            code="000858",
            name="Wuliangye",
            trade_date=day,
            open_price=163.2,
            close=168.0,
            prev_close=162.4,
            ma20=154.0,
            ma60=147.0,
            ma60_slope=0.002,
            return_20d=0.11,
            return_60d=0.18,
            volatility_20d=0.024,
            avg_turnover_20d=2_200_000_000,
            turnover_rate_20d=0.019,
            relative_strength=0.81,
            listing_days=5200,
            info_risk="earnings_beat",
            pe_rank=0.60,
            roe_rank=0.82,
            cashflow_rank=0.78,
        ),
        StockSnapshot(
            code="002415",
            name="Hikvision",
            trade_date=day,
            open_price=38.8,
            close=39.4,
            prev_close=38.6,
            ma20=37.1,
            ma60=35.8,
            ma60_slope=0.001,
            return_20d=0.07,
            return_60d=0.10,
            volatility_20d=0.026,
            avg_turnover_20d=1_250_000_000,
            turnover_rate_20d=0.014,
            relative_strength=0.63,
            listing_days=4200,
            pe_rank=0.67,
            roe_rank=0.73,
            cashflow_rank=0.71,
        ),
        StockSnapshot(
            code="300750",
            name="CATL GEM excluded",
            trade_date=day,
            open_price=208.0,
            close=210.0,
            prev_close=207.0,
            ma20=200.0,
            ma60=188.0,
            ma60_slope=0.004,
            return_20d=0.12,
            return_60d=0.25,
            volatility_20d=0.03,
            avg_turnover_20d=7_000_000_000,
            turnover_rate_20d=0.017,
            relative_strength=0.90,
            listing_days=3000,
        ),
        StockSnapshot(
            code="600000",
            name="Pudong Bank",
            trade_date=day,
            open_price=8.48,
            close=8.4,
            prev_close=8.5,
            ma20=8.6,
            ma60=8.7,
            ma60_slope=-0.001,
            return_20d=-0.03,
            return_60d=-0.04,
            volatility_20d=0.011,
            avg_turnover_20d=520_000_000,
            turnover_rate_20d=0.004,
            relative_strength=0.25,
            listing_days=6500,
            pe_rank=0.80,
            roe_rank=0.42,
            cashflow_rank=0.50,
        ),
        StockSnapshot(
            code="603259",
            name="WuXi AppTec",
            trade_date=day,
            open_price=63.0,
            close=61.2,
            prev_close=63.3,
            ma20=66.5,
            ma60=70.0,
            ma60_slope=-0.003,
            return_20d=-0.12,
            return_60d=-0.20,
            volatility_20d=0.040,
            avg_turnover_20d=1_800_000_000,
            turnover_rate_20d=0.035,
            relative_strength=0.18,
            listing_days=2800,
            info_risk="investigation",
            pe_rank=0.45,
            roe_rank=0.62,
            cashflow_rank=0.58,
        ),
    ]


def load_sample_history(days: int = 8, start_date: date | None = None) -> list[list[StockSnapshot]]:
    first_day = _next_weekday(start_date or date.today())
    base = load_sample_snapshots(first_day)
    series: list[list[StockSnapshot]] = []
    drift_by_code = {
        "600519": 0.002,
        "000858": 0.006,
        "002415": 0.004,
        "300750": 0.005,
        "600000": -0.002,
        "603259": -0.006,
    }
    for offset in range(days):
        trade_date = _add_weekdays(first_day, offset)
        day_snapshots: list[StockSnapshot] = []
        for snapshot in base:
            drift = drift_by_code.get(snapshot.code, 0.0)
            change = 1 + drift * offset
            close = round(snapshot.close * change, 2)
            open_price = round(snapshot.prev_close * (1 + drift * offset * 0.6), 2)
            day_snapshots.append(
                replace(
                    snapshot,
                    trade_date=trade_date,
                    open_price=open_price,
                    close=close,
                    prev_close=round(snapshot.close * (1 + drift * (offset - 1)), 2) if offset else snapshot.prev_close,
                    return_20d=snapshot.return_20d + drift * offset,
                    return_60d=snapshot.return_60d + drift * offset,
                )
            )
        series.append(day_snapshots)
    return series


def _next_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _add_weekdays(start: date, offset: int) -> date:
    day = start
    added = 0
    while added < offset:
        day += timedelta(days=1)
        if day.weekday() < 5:
            added += 1
    return day
