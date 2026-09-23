from __future__ import annotations


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def rank01(values: dict[str, float], reverse: bool = False) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1], reverse=reverse)
    if len(ordered) == 1:
        return {ordered[0][0]: 1.0}
    denominator = len(ordered) - 1
    ranks: dict[str, float] = {}
    for index, (key, _) in enumerate(ordered):
        ranks[key] = 1 - index / denominator
    return ranks


def round_lot(shares: float) -> int:
    return max(0, int(shares // 100) * 100)

