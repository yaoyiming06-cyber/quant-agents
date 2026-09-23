from __future__ import annotations

from .config import UniverseConfig
from .models import StockSnapshot


def is_allowed_code(code: str, config: UniverseConfig) -> bool:
    if code.startswith(config.exclude_prefixes):
        return False
    return code.startswith(config.include_prefixes)


def filter_universe(snapshots: list[StockSnapshot], config: UniverseConfig) -> list[StockSnapshot]:
    result: list[StockSnapshot] = []
    for item in snapshots:
        if not is_allowed_code(item.code, config):
            continue
        if item.is_st or item.is_suspended:
            continue
        if item.listing_days < config.min_listing_days:
            continue
        if item.avg_turnover_20d < config.min_avg_turnover:
            continue
        if item.close < config.min_price:
            continue
        result.append(item)
    return result

