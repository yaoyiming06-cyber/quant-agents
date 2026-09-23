from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "initialCash": 1_000_000.0,
    "maxPositions": 5,
    "maxSingleWeight": 0.10,
    "maxTotalWeight": 0.50,
    "minBuyScore": 0.78,
    "marketRegimeScoreAdjustments": {
        "risk_off": 0.03,
        "divergent": 0.015,
        "neutral": 0.0,
        "risk_on": -0.01,
        "overheated": 0.02,
    },
    "marketRegimeTotalWeights": {
        "risk_off": 0.20,
        "divergent": 0.35,
        "neutral": 0.50,
        "risk_on": 0.60,
        "overheated": 0.30,
    },
    "marketRegimeSingleWeightMultipliers": {
        "risk_off": 0.60,
        "divergent": 0.80,
        "neutral": 1.00,
        "risk_on": 1.20,
        "overheated": 0.75,
    },
    "marketRegimeSingleWeightCaps": {
        "risk_off": 0.10,
        "divergent": 0.12,
        "neutral": 0.15,
        "risk_on": 0.18,
        "overheated": 0.12,
    },
    "positionConvictionMultipliers": {
        "normal": 1.00,
        "strong": 1.20,
        "elite": 1.45,
    },
    "strongConvictionScore": 0.82,
    "eliteConvictionScore": 0.86,
    "confirmationsRequired": 2,
    "stopLossPct": -8.0,
    "takeProfitTrimPct": 10.0,
    "slippagePct": 0.10,
    "commissionRate": 0.00025,
    "stampTaxRate": 0.0005,
    "minFee": 5.0,
    "tradeExecutionWindows": (("09:30", "11:30"), ("13:00", "15:00")),
    "exitPoolConfirmationsRequired": 2,
    "sectorConfirmationsRequired": 2,
    "sectorEmaAlpha": 0.35,
    "sectorBreakoutDeltaPct": 1.5,
    "sectorBreakdownDeltaPct": -1.5,
    "sectorStrongAvgPct": 1.2,
    "sectorWeakAvgPct": -1.0,
    "sectorStrongUpRatio": 0.65,
    "sectorWeakDownRatio": 0.65,
    "sectorOverheatedPct": 4.0,
    "sizingSafetyBufferPct": 0.50,
    "buyConfirmStartTime": "13:05",
    "earliestBuyTime": "13:20",
    "weakToStrongEnabled": True,
    "weakToStrongConfirmationsRequired": 2,
    "weakToStrongStartTime": "09:50",
    "weakToStrongEarliestBuyTime": "10:05",
    "weakToStrongLatestBuyTime": "11:20",
    "weakToStrongMinScore": 0.76,
    "weakToStrongScoreAdjustments": {
        "risk_off": 0.02,
        "divergent": 0.01,
        "neutral": 0.0,
        "risk_on": -0.01,
        "overheated": 0.02,
    },
    "weakToStrongSingleWeights": {
        "risk_off": 0.025,
        "divergent": 0.035,
        "neutral": 0.05,
        "risk_on": 0.06,
        "overheated": 0.03,
    },
    "weakToStrongTotalWeights": {
        "risk_off": 0.10,
        "divergent": 0.15,
        "neutral": 0.20,
        "risk_on": 0.25,
        "overheated": 0.12,
    },
    "pyramidingEnabled": True,
    "pyramidMaxAdds": 2,
    "pyramidFirstProfitPct": 3.0,
    "pyramidSecondProfitPct": 6.0,
    "pyramidAddWeight": 0.03,
    "pyramidMaxSingleWeight": 0.12,
    "pyramidSecondMinScore": 0.82,
    "weakToStrongInitialWeakPct": -0.8,
    "weakToStrongMinPctImprovement": 0.5,
    "weakToStrongMinPriceImprovementPct": 0.3,
    "weakToStrongMinRelativeOutperformancePct": 0.8,
    "weakToStrongMinVolumeRatio": 1.05,
    "weakToStrongMinSectorUpRatio": 0.45,
    "weakToStrongMinSectorImprovementPct": 0.2,
    "missedOpportunityGainThresholdPct": 3.0,
    "missedOpportunityOutperformThresholdPct": 1.5,
    "missedOpportunityReviewCadence": "weekly",
    "mainlineProbeEnabled": True,
    "mainlineProbeWeight": 0.025,
    "mainlineProbeTotalWeight": 0.15,
    "mainlineProbeMinScore": 0.76,
    "mainlineProbeScoreBuffer": 0.025,
    "mainlineProbeEarliestBuyTime": "13:05",
    "mainlineProbeMarketRegimes": ("risk_on", "overheated", "divergent"),
    "mainlineProbeSectorStatuses": ("accelerating", "sustained", "overheated"),
    "mainlineProbeMinRecognitionScore": 70.0,
}


def update_simulated_trading(
    preferred: list[dict[str, Any]],
    trade_ledger: dict[str, Any],
    state_path: Path,
    asset_version: str,
    latest_data_date: str | None,
    quote_quality: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
    execution_time: object | None = None,
    candidate_pool_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = load_state(state_path)
    cfg = merge_config(DEFAULT_CONFIG, state.get("config") or {}, config or {})
    state = repair_t_plus_one_violations(state, cfg)
    account = state.get("account") or {
        "initialCash": cfg["initialCash"],
        "cash": cfg["initialCash"],
        "realizedPnl": 0.0,
    }
    positions = {row["code"]: row for row in state.get("positions", []) if row.get("code")}
    fills = list(state.get("fills", []))[-200:]
    watchlist = state.get("watchlist") or {}
    weak_to_strong_watch = state.get("weakToStrongWatch") if isinstance(state.get("weakToStrongWatch"), dict) else {}
    exit_watch = state.get("exitWatch") if isinstance(state.get("exitWatch"), dict) else {}
    sector_watch = state.get("sectorWatch") if isinstance(state.get("sectorWatch"), dict) else {}
    prepare_day_pnl_baseline(account, state, cfg, latest_data_date)
    market_policy = build_market_policy(market_context, cfg)

    preferred_by_code = {row.get("code"): row for row in preferred if row.get("code")}
    ledger_by_code = latest_ledger_by_code(trade_ledger)
    sector_status_by_code = update_sector_watch(sector_watch, positions, preferred_by_code, ledger_by_code, asset_version, cfg)
    refresh_exit_watch(exit_watch, positions, preferred_by_code)
    events: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    execution_dt = execution_datetime(execution_time or asset_version)

    mark_positions(positions, preferred_by_code, ledger_by_code, latest_data_date)
    block_quotes = not quote_quality_passes(quote_quality)
    outside_execution_window = not within_trade_execution_window(execution_dt, cfg)
    block_trading = block_quotes or outside_execution_window
    update_watchlist(
        watchlist,
        preferred,
        asset_version,
        cfg,
        market_policy,
        count_confirmation=not block_trading,
    )
    update_weak_to_strong_watch(
        weak_to_strong_watch,
        preferred,
        asset_version,
        market_policy,
        cfg,
        count_confirmation=not block_trading,
    )

    if block_quotes and positions:
        events.append(event("guard", "ALL", "stale_quotes", "Blocked all simulated orders because quote quality is stale."))
    elif outside_execution_window:
        events.append(
            event(
                "guard",
                "ALL",
                "outside_trade_execution_window",
                "Blocked simulated orders outside A-share continuous trading hours.",
            )
        )
    if not block_trading:
        for code, position in list(positions.items()):
            stock = preferred_by_code.get(code)
            sector_status = sector_status_by_code.get(code, {})
            sector_pressure = sector_sell_pressure_confirmed(sector_status, cfg)
            price = current_price_for_code(code, preferred_by_code, ledger_by_code)
            if not price:
                events.append(event("hold", code, "missing_price", "No current price; skipped auto sell."))
                continue
            pnl = position_pnl_pct(position, price)
            sell_reason = None
            sell_shares = int(position.get("shares") or 0)
            if stock is None:
                exit_state = bump_exit_watch(exit_watch, code, asset_version)
                if should_hold_limit_up_position(code, stock, ledger_by_code):
                    events.append(
                        event(
                            "hold",
                            code,
                            "limit_up_hold",
                            "Skipped out-of-pool sell; current quote is at or near limit up.",
                        )
                    )
                    continue
                if sector_pressure:
                    sell_reason = sector_sell_reason(sector_status)
                elif sector_strength_supports_hold(sector_status):
                    events.append(event("hold", code, "sector_strength_hold", "Skipped out-of-pool sell; sector momentum is strengthening."))
                    continue
                elif int(exit_state.get("confirmations") or 0) < int(cfg.get("exitPoolConfirmationsRequired") or 2):
                    events.append(event("hold", code, "out_of_pool_watch", "Position left preferred pool; watching next confirmed round before selling."))
                    continue
                else:
                    sell_reason = "out_of_pool_confirmed"
            elif pnl <= cfg["stopLossPct"]:
                sell_reason = "stop_loss"
            elif sector_pressure:
                shares = round_lot(position["shares"] // 2)
                if pnl > 0 and shares > 0 and not position.get("trimmed"):
                    sell_reason = f"{sector_sell_reason(sector_status)}_trim"
                    sell_shares = shares
                else:
                    sell_reason = sector_sell_reason(sector_status)
            elif pnl >= cfg["takeProfitTrimPct"] and not position.get("trimmed"):
                shares = round_lot(position["shares"] // 2)
                if shares > 0:
                    sell_reason = "take_profit_trim"
                    sell_shares = shares
            if sell_reason:
                if sell_reason != "stop_loss" and should_hold_limit_up_position(code, stock, ledger_by_code):
                    events.append(
                        event(
                            "hold",
                            code,
                            "limit_up_hold",
                            f"Skipped {sell_reason}; current quote is at or near limit up.",
                        )
                    )
                    continue
                available_for_sell = position_available_shares(position, latest_data_date)
                if available_for_sell <= 0:
                    events.append(event("hold", code, "t_plus_one_locked", f"Skipped {sell_reason}; position is T+1 locked."))
                    continue
                sell_shares = min(sell_shares, available_for_sell)
                orders.append(make_order("sell", code, position.get("name", code), sell_shares, price, sell_reason))
                if sell_reason in {"take_profit_trim", "sector_breakdown_trim", "sector_cooling_trim"}:
                    position["trimmed"] = True

        planned_sell_shares = planned_sell_shares_by_code(orders)
        orders.extend(
            make_market_derisk_orders(
                account,
                positions,
                preferred_by_code,
                ledger_by_code,
                latest_data_date,
                market_policy,
                events,
                planned_sell_shares,
            )
        )

    for order in orders:
        fill = execute_order(order, account, positions, cfg, asset_version, latest_data_date, execution_dt)
        fills.append(fill)
        events.append(event("fill" if fill["status"] == "filled" else "reject", order["code"], fill["reason"], fill["status"]))

    buy_orders: list[dict[str, Any]] = []
    missed_reasons: dict[str, list[str]] = {}
    if not block_trading:
        planned_buy_value = 0.0
        buy_orders.extend(
            make_pyramid_orders(
                account,
                positions,
                preferred,
                watchlist,
                cfg,
                market_policy,
                planned_buy_value,
            )
        )
        planned_buy_value += sum(
            float(order.get("referencePrice") or 0) * int(order.get("shares") or 0)
            for order in buy_orders
            if order.get("side") == "buy"
        )
        for stock in sorted(preferred, key=lambda row: row.get("rank") or 99):
            code = stock.get("code")
            if not code or code in positions:
                continue
            signal = signal_status(
                stock,
                watchlist.get(code, {}),
                cfg,
                block_quotes,
                market_policy,
                "outside_trade_execution_window" if outside_execution_window else None,
                weak_to_strong_watch.get(code, {}),
            )
            if len(positions) + len([order for order in buy_orders if order["side"] == "buy"]) >= int(cfg["maxPositions"]):
                missed_reasons[code] = list(signal["blockers"]) + ["max_positions"]
                continue
            weak_signal = signal.get("weakToStrong") if isinstance(signal.get("weakToStrong"), dict) else {}
            use_weak_to_strong = bool(weak_signal.get("eligible")) and not signal["eligible"]
            mainline_probe = signal.get("mainlineProbe") if isinstance(signal.get("mainlineProbe"), dict) else {}
            use_mainline_probe = bool(mainline_probe.get("eligible")) and not signal["eligible"] and not use_weak_to_strong
            if not signal["eligible"] and not use_weak_to_strong and not use_mainline_probe:
                missed_reasons[code] = (
                    list(signal["blockers"])
                    + list(weak_signal.get("blockers") or [])
                    + list(mainline_probe.get("blockers") or [])
                )
                continue
            price = positive_float(stock.get("currentClose"))
            if not price:
                missed_reasons[code] = ["missing_price"]
                continue
            equity = account_equity(account, positions)
            sizing_buffer = max(0.0, 1 - float(cfg.get("sizingSafetyBufferPct") or 0) / 100.0)
            current_exposure = exposure_value(positions) + planned_buy_value
            max_total_value = equity * float(market_policy["maxTotalWeight"]) * sizing_buffer
            target_weight = float(
                (
                    weak_signal.get("targetWeight")
                    if use_weak_to_strong
                    else mainline_probe.get("targetWeight")
                    if use_mainline_probe
                    else signal.get("targetWeight")
                )
                or market_policy["maxSingleWeight"]
            )
            if use_weak_to_strong:
                channel_limit = equity * float(weak_signal.get("maxTotalWeight") or 0) * sizing_buffer
                channel_exposure = weak_to_strong_exposure_value(positions) + weak_to_strong_planned_value(buy_orders)
                max_total_value = min(max_total_value, current_exposure + max(0.0, channel_limit - channel_exposure))
            elif use_mainline_probe:
                channel_limit = equity * float(mainline_probe.get("maxTotalWeight") or 0) * sizing_buffer
                channel_exposure = mainline_probe_exposure_value(positions) + mainline_probe_planned_value(buy_orders)
                max_total_value = min(max_total_value, current_exposure + max(0.0, channel_limit - channel_exposure))
            max_single_value = equity * target_weight * sizing_buffer
            if current_exposure >= max_total_value:
                missed_reasons[code] = ["exposure_limit"]
                continue
            target_value = min(
                max_single_value,
                max(0.0, max_total_value - current_exposure),
            )
            shares = round_lot(int(target_value / price))
            if shares > 0:
                buy_orders.append(
                    make_order(
                        "buy",
                        code,
                        stock.get("name", code),
                        shares,
                        price,
                        "weak_to_strong_confirmed"
                        if use_weak_to_strong
                        else "mainline_probe"
                        if use_mainline_probe
                        else "confirmed_signal",
                        {
                            "targetWeight": target_weight,
                            "positionConviction": "weak_to_strong"
                            if use_weak_to_strong
                            else "mainline_probe"
                            if use_mainline_probe
                            else signal.get("positionConviction"),
                            "positionSizingReasons": weak_signal.get("reasons")
                            if use_weak_to_strong
                            else mainline_probe.get("reasons")
                            if use_mainline_probe
                            else signal.get("positionSizingReasons"),
                            "riskDiscount": 1.0
                            if use_weak_to_strong
                            else mainline_probe.get("riskDiscount")
                            if use_mainline_probe
                            else signal.get("riskDiscount"),
                            "entryChannel": "weak_to_strong"
                            if use_weak_to_strong
                            else "mainline_probe"
                            if use_mainline_probe
                            else "trend_confirmation",
                        },
                    )
                )
                planned_buy_value += shares * price
            else:
                missed_reasons[code] = ["lot_size_or_cash"]
    elif block_quotes and preferred:
        events.append(event("guard", "ALL", "stale_quotes", "Blocked new buys because quote quality is stale."))
        missed_reasons = {stock.get("code"): ["stale_quotes"] for stock in preferred if stock.get("code") and stock.get("code") not in positions}
    elif outside_execution_window and preferred:
        missed_reasons = {
            stock.get("code"): ["outside_trade_execution_window"]
            for stock in preferred
            if stock.get("code") and stock.get("code") not in positions
        }

    for order in buy_orders:
        fill = execute_order(order, account, positions, cfg, asset_version, latest_data_date, execution_dt)
        fills.append(fill)
        events.append(event("fill" if fill["status"] == "filled" else "reject", order["code"], fill["reason"], fill["status"]))

    mark_positions(positions, preferred_by_code, ledger_by_code, latest_data_date)
    annotate_position_sizing(positions, preferred_by_code, watchlist, cfg, market_policy)
    pending = [
        signal_status(
            stock,
            watchlist.get(stock.get("code"), {}),
            cfg,
            block_quotes,
            market_policy,
            "outside_trade_execution_window" if outside_execution_window else None,
            weak_to_strong_watch.get(stock.get("code"), {}),
        )
        for stock in preferred
    ]
    missed = update_missed_opportunities(
        state.get("missedOpportunities") if isinstance(state.get("missedOpportunities"), list) else [],
        preferred,
        pending,
        missed_reasons,
        positions,
        latest_data_date,
        asset_version,
        market_policy,
        cfg,
    )
    missed_summary = build_missed_opportunity_summary(missed, positions, latest_data_date, cfg)
    result = build_state(
        cfg,
        account,
        positions,
        fills,
        orders + buy_orders,
        pending,
        watchlist,
        weak_to_strong_watch,
        exit_watch,
        sector_watch,
        events,
        asset_version,
        latest_data_date,
        quote_quality,
        block_trading,
        market_policy,
        missed,
        missed_summary,
        state.get("dailyReturns") if isinstance(state.get("dailyReturns"), list) else [],
        candidate_pool_quality=candidate_pool_quality,
    )
    write_state(state_path, result)
    return result


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def merge_config(*configs: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for config in configs:
        for key, value in config.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
    return merged


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def prepare_day_pnl_baseline(
    account: dict[str, Any],
    state: dict[str, Any],
    cfg: dict[str, Any],
    latest_data_date: str | None,
) -> None:
    current_date = latest_data_date or state.get("latestDataDate") or account.get("dayPnlDate")
    if not current_date:
        return

    previous_date = account.get("dayPnlDate") or state.get("latestDataDate")
    if previous_date != current_date:
        account["dayPnlDate"] = current_date
        account["dayStartEquity"] = round(previous_account_equity(account, cfg), 2)
        return

    account["dayPnlDate"] = current_date
    if positive_float(account.get("dayStartEquity")) is None:
        account["dayStartEquity"] = round(float(account.get("initialCash") or cfg["initialCash"]), 2)


def previous_account_equity(account: dict[str, Any], cfg: dict[str, Any]) -> float:
    equity = positive_float(account.get("equity"))
    if equity is not None:
        return equity
    cash = float(account.get("cash") or 0)
    position_value = float(account.get("positionValue") or 0)
    if cash or position_value:
        return cash + position_value
    return float(account.get("initialCash") or cfg["initialCash"])


def build_market_policy(market_context: dict[str, Any] | None, cfg: dict[str, Any]) -> dict[str, Any]:
    values = [
        value
        for item in ((market_context or {}).get("items") or [])
        for value in [numeric_float(item.get("pctChange"))]
        if value is not None
    ]
    if not values:
        regime = "neutral"
        avg_change = None
    else:
        avg_change = round(sum(values) / len(values), 2)
        positive_count = sum(1 for value in values if value > 0)
        all_positive = positive_count == len(values)
        mixed_market = positive_count > 0 and positive_count < len(values)
        if avg_change <= -0.7 or (positive_count == 0 and avg_change <= -0.35):
            regime = "risk_off"
        elif mixed_market:
            regime = "divergent"
        elif all_positive and avg_change >= 1.5:
            regime = "overheated"
        elif all_positive and avg_change >= 0.5:
            regime = "risk_on"
        else:
            regime = "neutral"

    score_adjustments = cfg.get("marketRegimeScoreAdjustments") or {}
    total_weights = cfg.get("marketRegimeTotalWeights") or {}
    single_multipliers = cfg.get("marketRegimeSingleWeightMultipliers") or {}
    min_score = float(cfg.get("minBuyScore") or 0) + float(score_adjustments.get(regime, 0) or 0)
    max_total_weight = float(total_weights.get(regime, cfg.get("maxTotalWeight") or 0))
    max_single_weight = float(cfg.get("maxSingleWeight") or 0) * float(single_multipliers.get(regime, 1.0) or 1.0)
    single_caps = cfg.get("marketRegimeSingleWeightCaps") or {}
    return {
        "regime": regime,
        "avgIndexPctChange": avg_change,
        "minBuyScore": round(min_score, 4),
        "baseMinBuyScore": float(cfg.get("minBuyScore") or 0),
        "maxTotalWeight": round(max_total_weight, 4),
        "maxSingleWeight": round(max_single_weight, 4),
        "maxSingleWeightCap": round(float(single_caps.get(regime, max_single_weight) or max_single_weight), 4),
        "source": "market_indices" if values else "neutral_fallback",
    }


def repair_t_plus_one_violations(state: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    fills = state.get("fills") if isinstance(state, dict) else None
    if not fills:
        return state

    account = {
        "initialCash": float((state.get("account") or {}).get("initialCash") or cfg["initialCash"]),
        "cash": float((state.get("account") or {}).get("initialCash") or cfg["initialCash"]),
        "realizedPnl": 0.0,
    }
    positions: dict[str, dict[str, Any]] = {}
    repaired: list[dict[str, Any]] = []
    changed = False

    for fill in fills:
        row = dict(fill)
        if row.get("status") != "filled":
            repaired.append(row)
            continue
        side = row.get("side")
        code = row.get("code")
        shares = int(row.get("shares") or 0)
        price = positive_float(row.get("price"))
        gross = positive_float(row.get("gross")) or ((price or 0) * shares)
        fee = float(row.get("fee") or 0)
        fill_dt = fill_execution_datetime(row)
        trade_date = trade_date_from_datetime(fill_dt) or fill_trade_date(row)
        if trade_date and row.get("tradeDate") != trade_date:
            row["tradeDate"] = trade_date
            changed = True
        if not code or not side or shares <= 0 or not price:
            repaired.append(row)
            continue

        if side == "buy":
            if fill_dt is not None and not within_trade_execution_window(fill_dt, cfg):
                repaired.append(rejected_replay_fill(row, "outside_trade_execution_window_after_repair"))
                changed = True
                continue
            if fill_dt is not None and not buy_fill_time_allowed(row, fill_dt, cfg):
                repaired.append(rejected_replay_fill(row, "before_buy_window_after_repair"))
                changed = True
                continue
            total = gross + fee
            if code not in positions and len(positions) >= int(cfg["maxPositions"]):
                repaired.append(rejected_replay_fill(row, "max_positions_after_t_plus_one_repair"))
                changed = True
                continue
            if total > float(account.get("cash") or 0):
                repaired.append(rejected_replay_fill(row, "cash_not_enough_after_t_plus_one_repair"))
                changed = True
                continue
            account["cash"] = round(float(account.get("cash") or 0) - total, 2)
            old = positions.get(code)
            if old:
                ensure_position_lots(old)
                new_shares = int(old.get("shares") or 0) + shares
                cost_basis = float(old.get("costBasis") or 0) + total
                lots = list(old.get("lots") or [])
            else:
                new_shares = shares
                cost_basis = total
                lots = []
            lots.append(position_lot(shares, total, trade_date, row.get("time")))
            positions[code] = {
                "code": code,
                "name": row.get("name", code),
                "shares": new_shares,
                "avgCost": round(cost_basis / new_shares, 3),
                "costBasis": round(cost_basis, 2),
                "entryTime": old.get("entryTime") if old else row.get("time"),
                "entryTradeDate": old.get("entryTradeDate") if old else trade_date,
                "lastTradeTime": row.get("time"),
                "trimmed": bool(old.get("trimmed")) if old else False,
                "lots": lots,
                "pyramidLevel": int(row.get("pyramidLevel") or old.get("pyramidLevel") or 0) if old else int(row.get("pyramidLevel") or 0),
            }
            for key in ("targetWeight", "positionConviction", "positionSizingReasons", "riskDiscount", "entryChannel"):
                if key in row:
                    positions[code][key] = row[key]
            repaired.append(row)
            continue

        if side == "sell":
            if fill_dt is not None and not within_trade_execution_window(fill_dt, cfg):
                repaired.append(rejected_replay_fill(row, "outside_trade_execution_window_after_repair"))
                changed = True
                continue
            old = positions.get(code)
            if not old:
                repaired.append(rejected_replay_fill(row, "no_position_after_t_plus_one_repair"))
                changed = True
                continue
            if position_available_shares(old, trade_date) <= 0:
                repaired.append(rejected_replay_fill(row, "t_plus_one_locked"))
                changed = True
                continue
            sell_shares = min(shares, position_available_shares(old, trade_date))
            sell_gross = round(price * sell_shares, 2)
            account["cash"] = round(float(account.get("cash") or 0) + sell_gross - fee, 2)
            cost_reduction = float(old.get("avgCost") or 0) * sell_shares
            account["realizedPnl"] = round(float(account.get("realizedPnl") or 0) + sell_gross - fee - cost_reduction, 2)
            remaining = int(old.get("shares") or 0) - sell_shares
            if remaining <= 0:
                positions.pop(code, None)
            else:
                consume_position_lots(old, sell_shares, trade_date)
                old["shares"] = remaining
                old["costBasis"] = round(float(old.get("avgCost") or 0) * remaining, 2)
                old["lastTradeTime"] = row.get("time")
            repaired.append(row)
            continue

        repaired.append(row)

    if not changed:
        return state
    repaired_state = dict(state)
    repaired_state["account"] = account
    repaired_state["positions"] = list(positions.values())
    repaired_state["fills"] = repaired[-200:]
    repaired_state["latestFills"] = repaired[-20:]
    repaired_state["events"] = list(state.get("events", []))[-40:] + [
        event("repair", "ALL", "t_plus_one_repair", "Replayed simulated fills and rejected same-day sells.")
    ]
    return repaired_state


def rejected_replay_fill(fill: dict[str, Any], reason: str) -> dict[str, Any]:
    row = dict(fill)
    row["status"] = "rejected"
    row["originalReason"] = row.get("reason")
    row["reason"] = reason
    return row


def latest_ledger_by_code(trade_ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = trade_ledger.get("records", []) if isinstance(trade_ledger, dict) else []
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = row.get("code")
        if code and code not in result:
            result[code] = row
    return result


def refresh_exit_watch(
    exit_watch: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    preferred_by_code: dict[str, dict[str, Any]],
) -> None:
    for code in list(exit_watch):
        if code not in positions or code in preferred_by_code:
            exit_watch.pop(code, None)


def bump_exit_watch(exit_watch: dict[str, Any], code: str, asset_version: str) -> dict[str, Any]:
    old = exit_watch.get(code) if isinstance(exit_watch.get(code), dict) else {}
    confirmations = int(old.get("confirmations") or 0) + 1
    row = {
        "code": code,
        "confirmations": confirmations,
        "firstSeenAt": old.get("firstSeenAt") or asset_version,
        "lastSeenAt": asset_version,
    }
    exit_watch[code] = row
    return row


def update_sector_watch(
    sector_watch: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    preferred_by_code: dict[str, dict[str, Any]],
    ledger_by_code: dict[str, dict[str, Any]],
    asset_version: str,
    cfg: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    alpha = max(0.0, min(1.0, float(cfg.get("sectorEmaAlpha") or 0.35)))
    for code in positions:
        source = preferred_by_code.get(code) or ledger_by_code.get(code) or {}
        context = source.get("sectorContext") if isinstance(source.get("sectorContext"), dict) else {}
        if not context:
            old = sector_watch.get(code) if isinstance(sector_watch.get(code), dict) else {}
            if old:
                result[code] = old
            continue
        old = sector_watch.get(code) if isinstance(sector_watch.get(code), dict) else {}
        avg_pct = numeric_float(context.get("avgPctChange"))
        if avg_pct is None:
            continue
        quote_id = str(context.get("latestQuoteTime") or asset_version)
        if old.get("lastQuoteTime") == quote_id:
            row = dict(old)
            row["lastSeenAt"] = asset_version
            sector_watch[code] = row
            result[code] = row
            continue
        old_avg = numeric_float(old.get("avgPctEma"))
        baseline = old_avg
        if baseline is None:
            baseline = numeric_float(context.get("recentAvgPctChange") or context.get("avgPctBaseline"))
        delta = None if baseline is None else round(avg_pct - baseline, 4)
        candidate = classify_sector_state(context, baseline, cfg)
        confirmations = int(old.get("confirmations") or 0) + 1 if old.get("state") == candidate else 1
        ema = avg_pct if old_avg is None else round(old_avg * (1 - alpha) + avg_pct * alpha, 4)
        row = {
            "code": code,
            "theme": context.get("primaryTheme") or first_text(context.get("themes")) or old.get("theme"),
            "state": candidate,
            "confirmations": confirmations,
            "avgPctChange": avg_pct,
            "avgPctEma": ema,
            "deltaPct": delta,
            "upRatio": numeric_float(context.get("upRatio")),
            "downRatio": numeric_float(context.get("downRatio")),
            "hotScore": numeric_float(context.get("hotScore")),
            "flowStatus": context.get("flowStatus"),
            "flowRecognitionScore": numeric_float(context.get("flowRecognitionScore")),
            "netFlowRatio": numeric_float(context.get("netFlowRatio")),
            "flowDelta": numeric_float(context.get("flowDelta")),
            "peerCount": int(context.get("peerCount") or 0),
            "lastQuoteTime": quote_id,
            "lastSeenAt": asset_version,
        }
        sector_watch[code] = row
        result[code] = row
    for code in list(sector_watch):
        if code not in positions:
            sector_watch.pop(code, None)
    return result


def classify_sector_state(context: dict[str, Any], baseline: float | None, cfg: dict[str, Any]) -> str:
    flow_status = str(context.get("flowStatus") or "")
    if flow_status == "outflow":
        return "breakdown"
    if flow_status == "cooling":
        return "cooling"
    if flow_status == "overheated":
        return "hot_but_overheated"
    if flow_status in {"accelerating", "sustained"}:
        return "strong_breakout"
    avg_pct = numeric_float(context.get("avgPctChange"))
    if avg_pct is None:
        return "neutral"
    up_ratio = numeric_float(context.get("upRatio"))
    down_ratio = numeric_float(context.get("downRatio"))
    if down_ratio is None and up_ratio is not None:
        down_ratio = 1 - up_ratio
    delta = 0.0 if baseline is None else avg_pct - baseline
    if (
        delta <= float(cfg.get("sectorBreakdownDeltaPct") or -1.5)
        and avg_pct <= float(cfg.get("sectorWeakAvgPct") or -1.0)
        and (down_ratio is None or down_ratio >= float(cfg.get("sectorWeakDownRatio") or 0.65))
    ):
        return "breakdown"
    if avg_pct < 0 and delta < 0 and (down_ratio is not None and down_ratio >= 0.55):
        return "cooling"
    if avg_pct >= float(cfg.get("sectorOverheatedPct") or 4.0):
        return "hot_but_overheated"
    if (
        delta >= float(cfg.get("sectorBreakoutDeltaPct") or 1.5)
        and avg_pct >= float(cfg.get("sectorStrongAvgPct") or 1.2)
        and (up_ratio is None or up_ratio >= float(cfg.get("sectorStrongUpRatio") or 0.65))
    ):
        return "strong_breakout"
    if up_ratio is not None and 0.35 < up_ratio < 0.65:
        return "divergent"
    return "neutral"


def sector_sell_pressure_confirmed(status: dict[str, Any], cfg: dict[str, Any]) -> bool:
    fast_outflow = (
        status.get("state") == "breakdown"
        and status.get("flowStatus") == "outflow"
        and (numeric_float(status.get("netFlowRatio")) or 0) <= -3.0
    )
    return (
        status.get("state") in {"breakdown", "cooling"}
        and (
            fast_outflow
            or int(status.get("confirmations") or 0) >= int(cfg.get("sectorConfirmationsRequired") or 2)
        )
    )


def sector_strength_supports_hold(status: dict[str, Any]) -> bool:
    return status.get("state") == "strong_breakout" and int(status.get("confirmations") or 0) >= 1


def sector_sell_reason(status: dict[str, Any]) -> str:
    return "sector_cooling" if status.get("state") == "cooling" else "sector_breakdown"


def first_text(values: Any) -> str | None:
    if isinstance(values, list):
        for value in values:
            if value:
                return str(value)
    return None


def update_watchlist(
    watchlist: dict[str, Any],
    preferred: list[dict[str, Any]],
    asset_version: str,
    cfg: dict[str, Any],
    market_policy: dict[str, Any],
    count_confirmation: bool = True,
) -> None:
    active_codes = {row.get("code") for row in preferred}
    for code in list(watchlist):
        if code not in active_codes:
            watchlist.pop(code, None)
    for stock in preferred:
        code = stock.get("code")
        if not code:
            continue
        quote_id = stock.get("latestQuoteTime") or asset_version
        old = watchlist.get(code, {})
        count = int(old.get("confirmations") or 0)
        confirm_blockers = stock_buy_blockers(stock, market_policy)
        quote_dt = parse_quote_datetime(quote_id)
        count_allowed = quote_dt is None or time_at_or_after(quote_dt, str(cfg.get("buyConfirmStartTime") or "00:00"))
        if not count_confirmation or confirm_blockers or not count_allowed:
            count = 0
        elif old.get("lastQuoteTime") == quote_id:
            count = count
        elif count <= 0 or not old.get("lastQuoteTime"):
            count = 1
        elif quote_times_are_consecutive(old.get("lastQuoteTime"), quote_id):
            count += 1
        else:
            count = 1
        watchlist[code] = {
            "code": code,
            "name": stock.get("name", code),
            "firstSeenAt": old.get("firstSeenAt") or quote_id,
            "lastQuoteTime": quote_id,
            "lastAssetVersion": asset_version,
            "confirmations": count,
            "confirmationBlockers": confirm_blockers,
            "rank": stock.get("rank"),
            "score": effective_stock_score(stock),
        }


def update_weak_to_strong_watch(
    watchlist: dict[str, Any],
    preferred: list[dict[str, Any]],
    asset_version: str,
    market_policy: dict[str, Any],
    cfg: dict[str, Any],
    count_confirmation: bool = True,
) -> None:
    active_codes = {row.get("code") for row in preferred}
    for code in list(watchlist):
        if code not in active_codes:
            watchlist.pop(code, None)
    for stock in preferred:
        code = stock.get("code")
        if not code:
            continue
        observation = weak_to_strong_observation(stock, market_policy, asset_version)
        quote_time = observation.get("quoteTime")
        quote_dt = parse_quote_datetime(quote_time)
        old = watchlist.get(code) if isinstance(watchlist.get(code), dict) else {}
        observations = list(old.get("observations") or [])
        if observations and observation_trade_date(observations[-1]) != observation_trade_date(observation):
            old = {}
            observations = []
        if observations and observations[-1].get("quoteTime") == quote_time:
            continue
        observations.append(observation)
        observations = observations[-6:]

        baseline_weak = bool(old.get("baselineWeak")) or any(
            weak_to_strong_observation_is_weak(row, cfg) for row in observations[:-1] or observations
        )
        hard_blockers = weak_to_strong_hard_blockers(stock)
        reasons: list[str] = []
        confirms = int(old.get("confirmations") or 0)
        state = "baseline" if len(observations) == 1 else "watching"
        if hard_blockers:
            confirms = 0
            state = "blocked"
        elif len(observations) >= 2 and baseline_weak:
            prior = observations[-2]
            pattern_passes, reasons = weak_to_strong_pattern_passes(prior, observation, cfg)
            start_allowed = quote_dt is None or time_at_or_after(quote_dt, str(cfg.get("weakToStrongStartTime") or "09:50"))
            if count_confirmation and start_allowed and pattern_passes:
                confirms = confirms + 1 if quote_times_are_consecutive(prior.get("quoteTime"), quote_time) else 1
                state = "confirmed" if confirms >= int(cfg.get("weakToStrongConfirmationsRequired") or 2) else "confirming"
            else:
                confirms = 0
        elif not baseline_weak:
            confirms = 0
            state = "observing"

        watchlist[code] = {
            "code": code,
            "name": stock.get("name", code),
            "state": state,
            "baselineWeak": baseline_weak,
            "confirmations": confirms,
            "requiredConfirmations": int(cfg.get("weakToStrongConfirmationsRequired") or 2),
            "reasons": reasons,
            "blockers": list(dict.fromkeys(hard_blockers)),
            "firstSeenAt": old.get("firstSeenAt") or quote_time,
            "lastQuoteTime": quote_time,
            "lastAssetVersion": asset_version,
            "observations": observations,
        }


def weak_to_strong_observation(stock: dict[str, Any], market_policy: dict[str, Any], asset_version: str) -> dict[str, Any]:
    basic = stock.get("basic") if isinstance(stock.get("basic"), dict) else {}
    context = stock.get("context") if isinstance(stock.get("context"), dict) else {}
    quote = context.get("quote") if isinstance(context.get("quote"), dict) else {}
    sector = stock.get("sectorContext") if isinstance(stock.get("sectorContext"), dict) else {}
    price = positive_float(stock.get("currentClose"))
    pct_change = numeric_float(stock.get("todayPctChange"))
    market_avg = numeric_float(market_policy.get("avgIndexPctChange"))
    return {
        "quoteTime": stock.get("latestQuoteTime") or asset_version,
        "price": price,
        "pctChange": pct_change,
        "openPrice": positive_float(quote.get("open")) or positive_float(basic.get("open")),
        "avgPrice": positive_float(quote.get("avg_price")),
        "volumeRatio": numeric_float(quote.get("volume_ratio")) or numeric_float(basic.get("latestVolumeRatio")),
        "sectorAvgPct": numeric_float(sector.get("avgPctChange")),
        "sectorUpRatio": numeric_float(sector.get("upRatio")),
        "sectorFlowStatus": sector.get("flowStatus"),
        "marketAvgPct": market_avg,
        "relativeOutperformancePct": round(pct_change - market_avg, 4)
        if pct_change is not None and market_avg is not None
        else None,
    }


def observation_trade_date(observation: dict[str, Any]) -> str | None:
    value = parse_quote_datetime(observation.get("quoteTime"))
    return value.date().isoformat() if value is not None else None


def weak_to_strong_observation_is_weak(observation: dict[str, Any], cfg: dict[str, Any]) -> bool:
    pct_change = numeric_float(observation.get("pctChange"))
    price = positive_float(observation.get("price"))
    open_price = positive_float(observation.get("openPrice"))
    avg_price = positive_float(observation.get("avgPrice"))
    return bool(
        (pct_change is not None and pct_change <= float(cfg.get("weakToStrongInitialWeakPct") or -0.8))
        or (price is not None and open_price is not None and price < open_price)
        or (price is not None and avg_price is not None and price < avg_price)
    )


def weak_to_strong_hard_blockers(stock: dict[str, Any]) -> list[str]:
    blockers = stock_confirmation_blockers(stock)
    health = stock.get("intradayHealth") if isinstance(stock.get("intradayHealth"), dict) else {}
    blockers.extend(str(item) for item in health.get("blockers") or [])
    sector = stock.get("sectorContext") if isinstance(stock.get("sectorContext"), dict) else {}
    if sector.get("flowStatus") in {"cooling", "outflow"}:
        blockers.append("sector_flow_outflow")
    return list(dict.fromkeys(blockers))


def weak_to_strong_pattern_passes(
    prior: dict[str, Any],
    current: dict[str, Any],
    cfg: dict[str, Any],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    prior_price = positive_float(prior.get("price"))
    current_price = positive_float(current.get("price"))
    prior_pct = numeric_float(prior.get("pctChange"))
    current_pct = numeric_float(current.get("pctChange"))
    if prior_price is None or current_price is None or prior_pct is None or current_pct is None:
        return False, reasons

    price_improvement = (current_price / prior_price - 1) * 100
    if price_improvement < float(cfg.get("weakToStrongMinPriceImprovementPct") or 0.3):
        return False, reasons
    reasons.append("price_rising")
    if current_pct - prior_pct < float(cfg.get("weakToStrongMinPctImprovement") or 0.5):
        return False, reasons
    reasons.append("momentum_improving")

    relative = numeric_float(current.get("relativeOutperformancePct"))
    if relative is None or relative < float(cfg.get("weakToStrongMinRelativeOutperformancePct") or 0.8):
        return False, reasons
    reasons.append("outperforming_market")

    avg_price = positive_float(current.get("avgPrice"))
    open_price = positive_float(current.get("openPrice"))
    references = [value for value in (avg_price, open_price) if value is not None]
    if references and not any(current_price >= value for value in references):
        return False, reasons
    reasons.append("reference_reclaimed")

    volume_ratio = numeric_float(current.get("volumeRatio"))
    prior_volume = numeric_float(prior.get("volumeRatio"))
    if volume_ratio is None or volume_ratio < float(cfg.get("weakToStrongMinVolumeRatio") or 1.05):
        return False, reasons
    if prior_volume is not None and volume_ratio < prior_volume - 0.10:
        return False, reasons
    reasons.append("volume_confirmed")

    flow_status = str(current.get("sectorFlowStatus") or "")
    current_sector = numeric_float(current.get("sectorAvgPct"))
    prior_sector = numeric_float(prior.get("sectorAvgPct"))
    up_ratio = numeric_float(current.get("sectorUpRatio"))
    prior_up_ratio = numeric_float(prior.get("sectorUpRatio"))
    sector_improving = (
        current_sector is not None
        and prior_sector is not None
        and current_sector - prior_sector >= float(cfg.get("weakToStrongMinSectorImprovementPct") or 0.2)
        and (up_ratio is None or up_ratio >= float(cfg.get("weakToStrongMinSectorUpRatio") or 0.45))
        and (prior_up_ratio is None or up_ratio is None or up_ratio >= prior_up_ratio)
    )
    if flow_status not in {"accelerating", "sustained"} and not sector_improving:
        return False, reasons
    reasons.append("sector_improving")
    return True, reasons


def weak_to_strong_signal_status(
    stock: dict[str, Any],
    watch: dict[str, Any],
    cfg: dict[str, Any],
    block_new_buys: bool,
    market_policy: dict[str, Any],
) -> dict[str, Any]:
    blockers = list(watch.get("blockers") or [])
    if not cfg.get("weakToStrongEnabled", True):
        blockers.append("weak_to_strong_disabled")
    if block_new_buys:
        blockers.append("stale_quotes")
    confirmations = int(watch.get("confirmations") or 0)
    required = int(cfg.get("weakToStrongConfirmationsRequired") or 2)
    if confirmations < required:
        blockers.append("weak_to_strong_needs_confirmation")
    quote_dt = parse_quote_datetime(watch.get("lastQuoteTime") or stock.get("latestQuoteTime"))
    if quote_dt is not None and not time_at_or_after(quote_dt, str(cfg.get("weakToStrongEarliestBuyTime") or "10:05")):
        blockers.append("weak_to_strong_before_window")
    if quote_dt is not None and not time_at_or_before(quote_dt, str(cfg.get("weakToStrongLatestBuyTime") or "11:20")):
        blockers.append("weak_to_strong_window_closed")
    regime = str(market_policy.get("regime") or "neutral")
    adjustments = cfg.get("weakToStrongScoreAdjustments") or {}
    min_score = float(cfg.get("weakToStrongMinScore") or 0.76) + float(adjustments.get(regime, 0) or 0)
    score_value = effective_stock_score(stock)
    if score_value is None or score_value < min_score:
        blockers.append("weak_to_strong_score_below_min")
    single_weights = cfg.get("weakToStrongSingleWeights") or {}
    total_weights = cfg.get("weakToStrongTotalWeights") or {}
    return {
        "state": watch.get("state") or "observing",
        "confirmations": confirmations,
        "requiredConfirmations": required,
        "eligible": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "reasons": list(watch.get("reasons") or []),
        "minScore": round(min_score, 4),
        "targetWeight": round(float(single_weights.get(regime, 0.05) or 0.05), 4),
        "maxTotalWeight": round(float(total_weights.get(regime, 0.20) or 0.20), 4),
    }


def signal_status(
    stock: dict[str, Any],
    watch: dict[str, Any],
    cfg: dict[str, Any],
    block_new_buys: bool,
    market_policy: dict[str, Any],
    execution_blocker: str | None = None,
    reversal_watch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    if block_new_buys:
        blockers.append("stale_quotes")
    if execution_blocker:
        blockers.append(execution_blocker)
    if int(watch.get("confirmations") or 0) < int(cfg["confirmationsRequired"]):
        blockers.append("needs_confirmation")
    quote_dt = parse_quote_datetime(watch.get("lastQuoteTime") or stock.get("latestQuoteTime"))
    if quote_dt is not None and not time_at_or_after(quote_dt, str(cfg.get("earliestBuyTime") or "00:00")):
        blockers.append("before_buy_window")
    blockers.extend(stock_buy_blockers(stock, market_policy))
    confirmations = int(watch.get("confirmations") or 0)
    required = int(cfg["confirmationsRequired"])
    sizing = position_sizing_profile(stock, confirmations, required, market_policy, cfg)
    weak_to_strong = weak_to_strong_signal_status(stock, reversal_watch or {}, cfg, block_new_buys, market_policy)
    mainline_probe = mainline_probe_signal_status(
        stock,
        blockers,
        confirmations,
        block_new_buys,
        market_policy,
        cfg,
        quote_dt,
    )
    return {
        "code": stock.get("code"),
        "name": stock.get("name"),
        "rank": stock.get("rank"),
        "price": stock.get("currentClose"),
        "score": effective_stock_score(stock),
        "baseScore": stock.get("baseScore", stock.get("finalScore")),
        "adjustedScore": stock.get("adjustedScore", effective_stock_score(stock)),
        "intradayHealth": stock.get("intradayHealth"),
        "minBuyScore": market_policy.get("minBuyScore"),
        "marketRegime": market_policy.get("regime"),
        "confirmations": confirmations,
        "requiredConfirmations": required,
        "targetWeight": sizing["targetWeight"],
        "positionConviction": sizing["positionConviction"],
        "positionSizingReasons": sizing["positionSizingReasons"],
        "riskDiscount": sizing["riskDiscount"],
        "weakToStrong": weak_to_strong,
        "mainlineProbe": mainline_probe,
        "eligible": not blockers,
        "blockers": blockers,
    }


def mainline_probe_signal_status(
    stock: dict[str, Any],
    base_blockers: list[str],
    confirmations: int,
    block_new_buys: bool,
    market_policy: dict[str, Any],
    cfg: dict[str, Any],
    quote_dt: datetime | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    blockers: list[str] = []
    if not cfg.get("mainlineProbeEnabled", True):
        blockers.append("mainline_probe_disabled")
    if block_new_buys:
        blockers.append("stale_quotes")
    if quote_dt is not None and not time_at_or_after(quote_dt, str(cfg.get("mainlineProbeEarliestBuyTime") or "13:05")):
        blockers.append("mainline_probe_before_window")

    profile = mainline_profile(stock, cfg)
    if not profile["isMainline"]:
        blockers.append("not_mainline")
    else:
        reasons.extend(profile["reasons"])

    regime = str(market_policy.get("regime") or "neutral")
    allowed_regimes = {str(item) for item in cfg.get("mainlineProbeMarketRegimes") or ()}
    if regime not in allowed_regimes:
        blockers.append("mainline_market_not_strong")

    score_value = effective_stock_score(stock)
    min_score = max(
        float(cfg.get("mainlineProbeMinScore") or 0),
        float(market_policy.get("minBuyScore") or 0) - float(cfg.get("mainlineProbeScoreBuffer") or 0),
    )
    if score_value is None:
        blockers.append("missing_score")
    elif score_value < min_score:
        blockers.append("mainline_score_too_low")
    else:
        reasons.append("score_near_gate")

    hard = set(mainline_probe_hard_blockers(base_blockers, stock))
    if hard:
        blockers.extend(sorted(hard))
    soft = [reason for reason in base_blockers if reason not in hard]
    disallowed_soft = [
        reason
        for reason in soft
        if reason not in {"needs_confirmation", "score_below_min", "danger_anomaly", "before_buy_window"}
    ]
    blockers.extend(disallowed_soft)
    if "danger_anomaly" in soft:
        reasons.append("danger_anomaly_position_penalty")
    if confirmations < int(cfg.get("confirmationsRequired") or 2):
        reasons.append("probe_before_full_confirmation")

    return {
        "eligible": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "reasons": list(dict.fromkeys(reasons or ["mainline_probe"])),
        "targetWeight": round(float(cfg.get("mainlineProbeWeight") or 0.025), 4),
        "maxTotalWeight": round(float(cfg.get("mainlineProbeTotalWeight") or 0.15), 4),
        "riskDiscount": 0.75 if "danger_anomaly" in soft else 1.0,
        "minScore": round(min_score, 4),
        "profile": profile,
    }


def mainline_profile(stock: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    sector = stock.get("sectorContext") if isinstance(stock.get("sectorContext"), dict) else {}
    flow_status = str(sector.get("flowStatus") or "")
    role = str(sector.get("flowRole") or "")
    recognition = numeric_float(sector.get("flowRecognitionScore"))
    hot_score = numeric_float(sector.get("hotScore"))
    up_ratio = numeric_float(sector.get("upRatio"))
    avg_pct = numeric_float(sector.get("avgPctChange"))
    recognition_min = float(cfg.get("mainlineProbeMinRecognitionScore") or 70.0)
    reasons: list[str] = []

    status_ok = flow_status in {"accelerating", "sustained", "overheated"}
    if status_ok:
        reasons.append(f"sector_{flow_status}")
    if role in {"leader", "core", "member"}:
        reasons.append(f"flow_role_{role}")
    if recognition is not None and recognition >= recognition_min:
        reasons.append("flow_recognized")
    if hot_score is not None and hot_score >= 180.0:
        reasons.append("sector_hot")
    if avg_pct is not None and avg_pct >= 1.2 and (up_ratio is None or up_ratio >= 0.60):
        reasons.append("sector_breadth_confirmed")

    is_mainline = bool(
        status_ok
        and (
            role in {"leader", "core"}
            or (recognition is not None and recognition >= recognition_min)
            or (hot_score is not None and hot_score >= 220.0)
            or (avg_pct is not None and avg_pct >= 2.0 and (up_ratio is None or up_ratio >= 0.65))
        )
    )
    return {
        "isMainline": is_mainline,
        "sector": sector.get("primaryTheme") or first_text(sector.get("themes")),
        "flowStatus": flow_status,
        "flowRole": role,
        "flowRecognitionScore": recognition,
        "hotScore": hot_score,
        "reasons": reasons,
    }


def mainline_probe_hard_blockers(blockers: list[str], stock: dict[str, Any]) -> list[str]:
    hard = {
        "risk_not_approved",
        "suspended",
        "limit_up",
        "limit_down",
        "severe_intraday_drop",
        "sector_breakdown",
        "sector_flow_outflow",
        "missing_price",
        "stale_quotes",
        "outside_trade_execution_window",
    }
    result = [reason for reason in blockers if reason in hard]
    states = stock.get("states") if isinstance(stock.get("states"), dict) else {}
    if states.get("isLimitDown") and "limit_down" not in result:
        result.append("limit_down")
    health = stock.get("intradayHealth") if isinstance(stock.get("intradayHealth"), dict) else {}
    for reason in health.get("blockers") or []:
        if str(reason) in hard and str(reason) not in result:
            result.append(str(reason))
    return result


def position_sizing_profile(
    stock: dict[str, Any],
    confirmations: int,
    required_confirmations: int,
    market_policy: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    conviction, reasons = position_conviction(stock, confirmations, required_confirmations, cfg)
    risk_discount, risk_reasons = position_risk_discount(stock)
    multipliers = cfg.get("positionConvictionMultipliers") or {}
    conviction_multiplier = float(multipliers.get(conviction, 1.0) or 1.0)
    base_weight = float(market_policy.get("maxSingleWeight") or cfg.get("maxSingleWeight") or 0)
    cap = float(market_policy.get("maxSingleWeightCap") or base_weight)
    target = min(cap, base_weight * conviction_multiplier * risk_discount)
    return {
        "targetWeight": round(max(0.0, target), 4),
        "positionConviction": conviction,
        "positionSizingReasons": reasons + risk_reasons,
        "riskDiscount": round(risk_discount, 4),
    }


def position_conviction(
    stock: dict[str, Any],
    confirmations: int,
    required_confirmations: int,
    cfg: dict[str, Any],
) -> tuple[str, list[str]]:
    score_value = effective_stock_score(stock) or 0.0
    trend = agent_score(stock, "trend")
    capital = agent_score(stock, "capital")
    sentiment = agent_score(stock, "sentiment")
    confirmed = confirmations >= required_confirmations
    trend_hard = trend is not None and trend >= 0.90
    capital_confirmed = capital is not None and capital >= 0.80
    sentiment_hot = sentiment is not None and sentiment >= 0.65
    reasons: list[str] = []
    if confirmed:
        reasons.append("rolling_confirmed")
    if score_value >= float(cfg.get("eliteConvictionScore") or 0.86):
        reasons.append("score_elite")
    elif score_value >= float(cfg.get("strongConvictionScore") or 0.82):
        reasons.append("score_strong")
    if trend_hard:
        reasons.append("trend_hard")
    if capital_confirmed:
        reasons.append("capital_confirmed")
    if sentiment_hot:
        reasons.append("sentiment_hot")

    if (
        confirmed
        and score_value >= float(cfg.get("eliteConvictionScore") or 0.86)
        and trend_hard
        and capital_confirmed
        and sentiment_hot
    ):
        return "elite", reasons
    if (
        confirmed
        and score_value >= float(cfg.get("strongConvictionScore") or 0.82)
        and (trend_hard or capital_confirmed or sentiment_hot)
    ):
        return "strong", reasons
    return "normal", reasons or ["base_position"]


def agent_score(stock: dict[str, Any], key: str) -> float | None:
    for agent in stock.get("agents") or []:
        if agent.get("key") == key:
            return numeric_float(agent.get("score"))
    return None


def position_risk_discount(stock: dict[str, Any]) -> tuple[float, list[str]]:
    discount = 1.0
    reasons: list[str] = []
    anomalies = stock.get("anomalies") or []
    if any(item.get("level") == "warn" for item in anomalies):
        discount *= 0.80
        reasons.append("warn_anomaly_discount")
    basic = stock.get("basic") or {}
    volatility = numeric_float(basic.get("volatility20d"))
    if volatility is not None and volatility >= 8.0:
        discount *= 0.85
        reasons.append("high_volatility_discount")
    today_change = abs(numeric_float(stock.get("todayPctChange")) or 0)
    if today_change >= 7.0:
        discount *= 0.85
        reasons.append("intraday_extreme_move_discount")
    return discount, reasons


def stock_buy_blockers(stock: dict[str, Any], market_policy: dict[str, Any]) -> list[str]:
    blockers = stock_confirmation_blockers(stock)
    health = stock.get("intradayHealth") if isinstance(stock.get("intradayHealth"), dict) else {}
    for blocker in health.get("blockers") or []:
        if blocker not in blockers:
            blockers.append(str(blocker))
    sector = stock.get("sectorContext") if isinstance(stock.get("sectorContext"), dict) else {}
    if sector.get("flowStatus") in {"cooling", "outflow"}:
        blockers.append("sector_flow_outflow")
    score_value = effective_stock_score(stock)
    if score_value is None:
        blockers.append("missing_score")
    elif score_value < float(market_policy.get("minBuyScore") or 0):
        blockers.append("score_below_min")
    return blockers


def effective_stock_score(stock: dict[str, Any]) -> float | None:
    adjusted = numeric_float(stock.get("adjustedScore"))
    if adjusted is not None:
        return adjusted
    return numeric_float(stock.get("finalScore"))


def stock_confirmation_blockers(stock: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if stock.get("riskCheck") != "approved":
        blockers.append("risk_not_approved")
    if not positive_float(stock.get("currentClose")):
        blockers.append("missing_price")
    states = stock.get("states") or {}
    if states.get("isSuspended"):
        blockers.append("suspended")
    if states.get("isLimitUp"):
        blockers.append("limit_up")
    if any(item.get("level") == "danger" for item in stock.get("anomalies", [])):
        blockers.append("danger_anomaly")
    return blockers


def quote_quality_passes(quote_quality: dict[str, Any] | None) -> bool:
    if not quote_quality:
        return False
    return (
        float(quote_quality.get("freshRatio") or 0) >= 0.95
        and int(quote_quality.get("preferredStaleCount") or 0) == 0
    )


def quote_times_are_consecutive(previous: object, current: object, max_gap_minutes: int = 30) -> bool:
    previous_dt = parse_quote_datetime(previous)
    current_dt = parse_quote_datetime(current)
    if previous_dt is None or current_dt is None:
        return str(previous or "") != str(current or "")
    if current_dt <= previous_dt:
        return False
    gap_minutes = (current_dt - previous_dt).total_seconds() / 60
    if gap_minutes <= max_gap_minutes:
        return True
    return (
        previous_dt.date() == current_dt.date()
        and previous_dt.hour == 11
        and 0 <= previous_dt.minute <= 35
        and current_dt.hour == 13
        and 0 <= current_dt.minute <= 20
    )


def parse_quote_datetime(value: object) -> datetime | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 14:
        return None
    try:
        return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def time_at_or_after(value: datetime, hhmm: str) -> bool:
    try:
        hour, minute = (int(part) for part in hhmm.split(":", 1))
    except ValueError:
        return True
    return (value.hour, value.minute) >= (hour, minute)


def time_at_or_before(value: datetime, hhmm: str) -> bool:
    try:
        hour, minute = (int(part) for part in hhmm.split(":", 1))
    except ValueError:
        return True
    return (value.hour, value.minute) <= (hour, minute)


def buy_fill_time_allowed(fill: dict[str, Any], fill_dt: datetime, cfg: dict[str, Any]) -> bool:
    if fill.get("reason") == "weak_to_strong_confirmed" or fill.get("entryChannel") == "weak_to_strong":
        return time_at_or_after(fill_dt, str(cfg.get("weakToStrongEarliestBuyTime") or "10:05"))
    if fill.get("reason") == "mainline_probe" or fill.get("entryChannel") == "mainline_probe":
        return time_at_or_after(fill_dt, str(cfg.get("mainlineProbeEarliestBuyTime") or "13:05"))
    return time_at_or_after(fill_dt, str(cfg.get("earliestBuyTime") or "00:00"))


def mark_positions(
    positions: dict[str, dict[str, Any]],
    preferred_by_code: dict[str, dict[str, Any]],
    ledger_by_code: dict[str, dict[str, Any]],
    latest_data_date: str | None,
) -> None:
    for code, position in positions.items():
        ensure_position_lots(position)
        price = current_price_for_code(code, preferred_by_code, ledger_by_code) or positive_float(position.get("marketPrice"))
        if not price:
            continue
        position["marketPrice"] = price
        position["marketValue"] = round(float(position.get("shares") or 0) * price, 2)
        position["unrealizedPnl"] = round(position["marketValue"] - float(position.get("costBasis") or 0), 2)
        position["pnlPct"] = position_pnl_pct(position, price)
        source = preferred_by_code.get(code) or ledger_by_code.get(code) or {}
        sector_context = source.get("sectorContext") if isinstance(source.get("sectorContext"), dict) else None
        if sector_context:
            position["sectorContext"] = sector_context
        available_shares = position_available_shares(position, latest_data_date)
        annotate_lot_availability(position, latest_data_date)
        position["availableShares"] = available_shares
        can_sell = available_shares > 0
        position["canSell"] = can_sell
        if can_sell:
            position.pop("sellLockReason", None)
        else:
            position["sellLockReason"] = "T+1"


def annotate_position_sizing(
    positions: dict[str, dict[str, Any]],
    preferred_by_code: dict[str, dict[str, Any]],
    watchlist: dict[str, Any],
    cfg: dict[str, Any],
    market_policy: dict[str, Any],
) -> None:
    required = int(cfg["confirmationsRequired"])
    for code, position in positions.items():
        stock = preferred_by_code.get(code)
        if not stock:
            continue
        if position.get("entryChannel") == "mainline_probe":
            position["targetWeight"] = min(
                float(position.get("targetWeight") or cfg.get("mainlineProbeWeight") or 0.025),
                float(cfg.get("mainlineProbeWeight") or 0.025),
            )
            position["positionConviction"] = "mainline_probe"
            position["positionSizingReasons"] = list(dict.fromkeys(position.get("positionSizingReasons") or ["mainline_probe"]))
            position["riskDiscount"] = float(position.get("riskDiscount") or 1.0)
            continue
        confirmations = max(int((watchlist.get(code) or {}).get("confirmations") or 0), required)
        sizing = position_sizing_profile(stock, confirmations, required, market_policy, cfg)
        position["targetWeight"] = sizing["targetWeight"]
        position["positionConviction"] = sizing["positionConviction"]
        position["positionSizingReasons"] = sizing["positionSizingReasons"]
        position["riskDiscount"] = sizing["riskDiscount"]


def current_price_for_code(
    code: str,
    preferred_by_code: dict[str, dict[str, Any]],
    ledger_by_code: dict[str, dict[str, Any]],
) -> float | None:
    stock = preferred_by_code.get(code)
    if stock:
        return positive_float(stock.get("currentClose"))
    row = ledger_by_code.get(code)
    if row:
        return positive_float(row.get("currentPrice") or row.get("exitPrice"))
    return None


def should_hold_limit_up_position(
    code: str,
    stock: dict[str, Any] | None,
    ledger_by_code: dict[str, dict[str, Any]],
) -> bool:
    if stock and is_limit_up_context(stock):
        return True
    row = ledger_by_code.get(code) or {}
    return is_limit_up_context(row)


def is_limit_up_context(row: dict[str, Any]) -> bool:
    states = row.get("states") if isinstance(row.get("states"), dict) else {}
    current_states = row.get("currentStates") if isinstance(row.get("currentStates"), dict) else {}
    if row.get("isLimitUp") or states.get("isLimitUp") or current_states.get("isLimitUp"):
        return True
    pct_change = numeric_float(row.get("currentPctChange") or row.get("todayPctChange"))
    if pct_change is not None and pct_change >= 9.8:
        return True
    price = positive_float(row.get("currentPrice") or row.get("currentClose"))
    limit_up = positive_float(row.get("currentLimitUp") or row.get("limitUpPrice") or row.get("limitUp"))
    return bool(price is not None and limit_up is not None and price >= limit_up - 0.01)


def make_market_derisk_orders(
    account: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    preferred_by_code: dict[str, dict[str, Any]],
    ledger_by_code: dict[str, dict[str, Any]],
    latest_data_date: str | None,
    market_policy: dict[str, Any],
    events: list[dict[str, Any]],
    planned_sell_shares: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    if market_policy.get("regime") not in {"risk_off", "divergent", "overheated"}:
        return []
    equity = account_equity(account, positions)
    if equity <= 0:
        return []
    target_value = equity * float(market_policy.get("maxTotalWeight") or 0)
    excess_value = exposure_value(positions) - target_value
    planned_sell_shares = planned_sell_shares or {}
    for code, shares in planned_sell_shares.items():
        position = positions.get(code)
        if not position or shares <= 0:
            continue
        price = current_price_for_code(code, preferred_by_code, ledger_by_code) or positive_float(position.get("marketPrice"))
        if price:
            excess_value -= min(int(position.get("shares") or 0), shares) * price
    if excess_value <= 0:
        return []

    rows = sorted(
        positions.values(),
        key=lambda row: (
            numeric_float((preferred_by_code.get(row.get("code")) or {}).get("finalScore")) or 0,
            numeric_float(row.get("pnlPct")) or 0,
        ),
    )
    orders: list[dict[str, Any]] = []
    for position in rows:
        if excess_value <= 0:
            break
        code = position.get("code")
        if not code:
            continue
        available_shares = max(0, position_available_shares(position, latest_data_date) - int(planned_sell_shares.get(code) or 0))
        if available_shares <= 0:
            continue
        if should_hold_limit_up_position(code, preferred_by_code.get(code), ledger_by_code):
            events.append(event("hold", code, "limit_up_hold", "Skipped market de-risk; current quote is at or near limit up."))
            continue
        if available_shares <= 0:
            events.append(event("hold", code, "t_plus_one_locked", "Skipped market de-risk; position is T+1 locked."))
            continue
        price = current_price_for_code(code, preferred_by_code, ledger_by_code) or positive_float(position.get("marketPrice"))
        if not price:
            continue
        sell_shares = round_lot(min(available_shares, int(excess_value / price)))
        if sell_shares <= 0:
            continue
        orders.append(make_order("sell", code, position.get("name", code), sell_shares, price, "market_de_risk"))
        excess_value -= sell_shares * price
    return orders


def make_pyramid_orders(
    account: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    preferred: list[dict[str, Any]],
    watchlist: dict[str, Any],
    cfg: dict[str, Any],
    market_policy: dict[str, Any],
    planned_buy_value: float = 0.0,
) -> list[dict[str, Any]]:
    if not cfg.get("pyramidingEnabled", True):
        return []
    equity = account_equity(account, positions)
    if equity <= 0:
        return []
    sizing_buffer = max(0.0, 1 - float(cfg.get("sizingSafetyBufferPct") or 0) / 100.0)
    max_total_value = equity * float(market_policy.get("maxTotalWeight") or cfg.get("maxTotalWeight") or 0) * sizing_buffer
    current_exposure = exposure_value(positions) + planned_buy_value
    if current_exposure >= max_total_value:
        return []

    orders: list[dict[str, Any]] = []
    for stock in sorted(preferred, key=lambda row: row.get("rank") or 99):
        code = stock.get("code")
        if not code or code not in positions:
            continue
        position = positions[code]
        order = pyramid_order_for_position(
            stock,
            position,
            account,
            positions,
            watchlist,
            cfg,
            market_policy,
            current_exposure,
            max_total_value,
        )
        if not order:
            continue
        orders.append(order)
        order_value = float(order.get("referencePrice") or 0) * int(order.get("shares") or 0)
        current_exposure += order_value
        if current_exposure >= max_total_value:
            break
    return orders


def pyramid_order_for_position(
    stock: dict[str, Any],
    position: dict[str, Any],
    account: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    watchlist: dict[str, Any],
    cfg: dict[str, Any],
    market_policy: dict[str, Any],
    current_exposure: float,
    max_total_value: float,
) -> dict[str, Any] | None:
    price = positive_float(stock.get("currentClose"))
    if not price:
        return None
    pnl = position_pnl_pct(position, price)
    level = int(position.get("pyramidLevel") or 0)
    max_adds = int(cfg.get("pyramidMaxAdds") or 0)
    if level >= max_adds:
        return None
    if pnl < pyramid_profit_threshold(level, cfg):
        return None
    if level >= 1:
        min_second_score = float(cfg.get("pyramidSecondMinScore") or cfg.get("strongConvictionScore") or 0.82)
        if (effective_stock_score(stock) or 0) < min_second_score:
            return None

    signal = signal_status(
        stock,
        watchlist.get(stock.get("code"), {}),
        cfg,
        False,
        market_policy,
        None,
        {},
    )
    if not signal.get("eligible"):
        return None
    if "danger_anomaly" in signal.get("blockers", []):
        return None

    equity = account_equity(account, positions)
    add_weight = float(cfg.get("pyramidAddWeight") or 0.0)
    cap_weight = min(
        float(cfg.get("pyramidMaxSingleWeight") or market_policy.get("maxSingleWeightCap") or 0),
        float(market_policy.get("maxSingleWeightCap") or cfg.get("maxSingleWeight") or 0),
    )
    current_value = float(position.get("marketValue") or 0)
    cap_value = equity * cap_weight
    room_single = max(0.0, cap_value - current_value)
    room_total = max(0.0, max_total_value - current_exposure)
    target_value = min(equity * add_weight, room_single, room_total, float(account.get("cash") or 0))
    shares = round_lot(int(target_value / price))
    if shares <= 0:
        return None
    return make_order(
        "buy",
        str(stock.get("code")),
        stock.get("name", stock.get("code")),
        shares,
        price,
        "pyramid_add",
        {
            "targetWeight": round(min(cap_weight, (current_value + shares * price) / equity), 4),
            "positionConviction": signal.get("positionConviction"),
            "positionSizingReasons": list(signal.get("positionSizingReasons") or []) + [f"pyramid_level_{level + 1}"],
            "riskDiscount": signal.get("riskDiscount"),
            "entryChannel": "pyramid",
            "pyramidLevel": level + 1,
            "pyramidProfitPct": pnl,
        },
    )


def pyramid_profit_threshold(current_level: int, cfg: dict[str, Any]) -> float:
    if current_level <= 0:
        return float(cfg.get("pyramidFirstProfitPct") or 3.0)
    return float(cfg.get("pyramidSecondProfitPct") or 6.0)


def planned_sell_shares_by_code(orders: list[dict[str, Any]]) -> dict[str, int]:
    planned: dict[str, int] = {}
    for order in orders:
        if order.get("side") != "sell":
            continue
        code = order.get("code")
        if not code:
            continue
        planned[str(code)] = planned.get(str(code), 0) + int(order.get("shares") or 0)
    return planned


def make_order(
    side: str,
    code: str,
    name: str,
    shares: int,
    reference_price: float,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order = {
        "side": side,
        "code": code,
        "name": name,
        "shares": int(shares),
        "referencePrice": round(float(reference_price), 3),
        "reason": reason,
    }
    if metadata:
        order.update(metadata)
    return order


def execute_order(
    order: dict[str, Any],
    account: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
    asset_version: str,
    latest_data_date: str | None,
    execution_dt: datetime | None = None,
) -> dict[str, Any]:
    side = order["side"]
    slippage = float(cfg["slippagePct"]) / 100.0
    price = float(order["referencePrice"]) * (1 + slippage if side == "buy" else 1 - slippage)
    price = round(price, 3)
    shares = int(order["shares"])
    gross = round(price * shares, 2)
    fee = max(float(cfg["minFee"]), gross * float(cfg["commissionRate"]))
    if side == "sell":
        fee += gross * float(cfg["stampTaxRate"])
    fee = round(fee, 2)
    stamp = round(gross * float(cfg["stampTaxRate"]), 2) if side == "sell" else 0.0
    execution_dt = execution_dt or execution_datetime(asset_version)
    now = execution_dt.isoformat(timespec="seconds")
    trade_date = trade_date_from_datetime(execution_dt) or latest_data_date
    fill = {
        "assetVersion": asset_version,
        "tradeDate": trade_date,
        "time": now,
        "side": side,
        "code": order["code"],
        "name": order.get("name", order["code"]),
        "shares": shares,
        "price": price,
        "gross": gross,
        "fee": fee,
        "stampTax": stamp,
        "status": "filled",
        "reason": order["reason"],
    }
    for key in (
        "targetWeight",
        "positionConviction",
        "positionSizingReasons",
        "riskDiscount",
        "entryChannel",
        "pyramidLevel",
        "pyramidProfitPct",
    ):
        if key in order:
            fill[key] = order[key]
    if not within_trade_execution_window(execution_dt, cfg):
        fill.update({"status": "rejected", "reason": "outside_trade_execution_window"})
        return fill
    if side == "buy":
        total = gross + fee
        if total > float(account.get("cash") or 0):
            fill.update({"status": "rejected", "reason": "cash_not_enough"})
            return fill
        account["cash"] = round(float(account.get("cash") or 0) - total, 2)
        old = positions.get(order["code"])
        if old:
            ensure_position_lots(old)
            old_shares = int(old.get("shares") or 0)
            new_shares = old_shares + shares
            cost_basis = float(old.get("costBasis") or 0) + total
            lots = list(old.get("lots") or [])
        else:
            new_shares = shares
            cost_basis = total
            lots = []
        lots.append(position_lot(shares, total, trade_date, now))
        positions[order["code"]] = {
            "code": order["code"],
            "name": order.get("name", order["code"]),
            "shares": new_shares,
            "avgCost": round(cost_basis / new_shares, 3),
            "costBasis": round(cost_basis, 2),
            "entryTime": old.get("entryTime") if old else now,
            "entryTradeDate": old.get("entryTradeDate") if old else latest_data_date,
            "lastTradeTime": now,
            "trimmed": bool(old.get("trimmed")) if old else False,
            "lots": lots,
            "pyramidLevel": int(order.get("pyramidLevel") or old.get("pyramidLevel") or 0) if old else int(order.get("pyramidLevel") or 0),
        }
        for key in ("targetWeight", "positionConviction", "positionSizingReasons", "riskDiscount", "entryChannel"):
            if key in order:
                positions[order["code"]][key] = order[key]
        return fill

    old = positions.get(order["code"])
    if not old:
        fill.update({"status": "rejected", "reason": "no_position"})
        return fill
    if position_available_shares(old, latest_data_date) <= 0:
        fill.update({"status": "rejected", "reason": "t_plus_one_locked"})
        return fill
    shares = min(shares, position_available_shares(old, latest_data_date))
    fill["shares"] = shares
    gross = round(price * shares, 2)
    fee = max(float(cfg["minFee"]), gross * float(cfg["commissionRate"])) + gross * float(cfg["stampTaxRate"])
    fee = round(fee, 2)
    fill.update({"gross": gross, "fee": fee, "stampTax": round(gross * float(cfg["stampTaxRate"]), 2)})
    account["cash"] = round(float(account.get("cash") or 0) + gross - fee, 2)
    cost_reduction = float(old.get("avgCost") or 0) * shares
    realized = gross - fee - cost_reduction
    account["realizedPnl"] = round(float(account.get("realizedPnl") or 0) + realized, 2)
    remaining = int(old.get("shares") or 0) - shares
    if remaining <= 0:
        positions.pop(order["code"], None)
    else:
        consume_position_lots(old, shares, latest_data_date)
        old["shares"] = remaining
        old["costBasis"] = round(float(old.get("avgCost") or 0) * remaining, 2)
        if remaining > 0:
            old["avgCost"] = round(float(old.get("costBasis") or 0) / remaining, 3)
        old["lastTradeTime"] = now
    return fill


def build_state(
    cfg: dict[str, Any],
    account: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    fills: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    watchlist: dict[str, Any],
    weak_to_strong_watch: dict[str, Any],
    exit_watch: dict[str, Any],
    sector_watch: dict[str, Any],
    events: list[dict[str, Any]],
    asset_version: str,
    latest_data_date: str | None,
    quote_quality: dict[str, Any] | None,
    block_trading: bool,
    market_policy: dict[str, Any],
    missed_opportunities: list[dict[str, Any]],
    missed_summary: dict[str, Any],
    old_daily_returns: list[dict[str, Any]] | None = None,
    candidate_pool_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    positions_list = sorted(positions.values(), key=lambda row: row.get("marketValue") or 0, reverse=True)
    position_value = round(sum(float(row.get("marketValue") or 0) for row in positions_list), 2)
    equity = round(float(account.get("cash") or 0) + position_value, 2)
    initial_cash = float(account.get("initialCash") or cfg["initialCash"])
    day_start_equity = positive_float(account.get("dayStartEquity")) or initial_cash
    day_pnl = round(equity - day_start_equity, 2)
    account_snapshot = {
        "initialCash": initial_cash,
        "cash": round(float(account.get("cash") or 0), 2),
        "positionValue": position_value,
        "equity": equity,
        "realizedPnl": round(float(account.get("realizedPnl") or 0), 2),
        "dayPnlDate": account.get("dayPnlDate") or latest_data_date,
        "dayStartEquity": round(day_start_equity, 2),
        "dayPnl": day_pnl,
        "dayPnlPct": round(day_pnl / day_start_equity * 100, 2) if day_start_equity else 0.0,
        "totalPnl": round(equity - initial_cash, 2),
        "totalPnlPct": round((equity / initial_cash - 1) * 100, 2) if initial_cash else 0.0,
        "exposurePct": round(position_value / equity * 100, 2) if equity else 0.0,
        "positions": len(positions_list),
    }
    return {
        "schemaVersion": 1,
        "mode": "auto_simulation",
        "strategy": cfg.get("strategyName"),
        "startDate": cfg.get("startDate"),
        "assetVersion": asset_version,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "latestDataDate": latest_data_date,
        "config": cfg,
        "guards": {
            "blockNewBuys": block_trading,
            "blockTrading": block_trading,
            "quoteQuality": quote_quality or {},
            "candidatePoolQuality": candidate_pool_quality or {},
        },
        "marketRegime": market_policy,
        "account": account_snapshot,
        "dailyReturns": update_daily_returns(old_daily_returns or [], account_snapshot, asset_version),
        "positions": positions_list,
        "orders": orders,
        "fills": fills[-200:],
        "latestFills": fills[-20:],
        "pendingSignals": pending,
        "missedOpportunities": missed_opportunities[-300:],
        "missedOpportunitySummary": missed_summary,
        "watchlist": watchlist,
        "weakToStrongWatch": weak_to_strong_watch,
        "exitWatch": exit_watch,
        "sectorWatch": sector_watch,
        "events": events[-50:],
    }


def update_daily_returns(
    old_daily_returns: list[dict[str, Any]],
    account: dict[str, Any],
    asset_version: str,
) -> list[dict[str, Any]]:
    date = normalize_trade_date(account.get("dayPnlDate"))
    if not date:
        return old_daily_returns[-260:]

    initial_cash = positive_float(account.get("initialCash")) or 0.0
    amount = round(float(account.get("dayPnl") or 0), 2)
    start_equity = round(float(account.get("dayStartEquity") or 0), 2)
    equity = round(float(account.get("equity") or 0), 2)
    by_date = {
        normalize_trade_date(row.get("date")): dict(row)
        for row in old_daily_returns
        if normalize_trade_date(row.get("date"))
    }
    by_date[date] = {
        "date": date,
        "amount": amount,
        "pct": round(amount / initial_cash * 100, 2) if initial_cash else 0.0,
        "dayStartEquity": start_equity,
        "equity": equity,
        "assetVersion": asset_version,
        "source": "account_mark_to_market",
    }
    return [by_date[key] for key in sorted(by_date)][-260:]


def account_equity(account: dict[str, Any], positions: dict[str, dict[str, Any]]) -> float:
    return float(account.get("cash") or 0) + exposure_value(positions)


def exposure_value(positions: dict[str, dict[str, Any]]) -> float:
    return sum(float(row.get("marketValue") or 0) for row in positions.values())


def weak_to_strong_exposure_value(positions: dict[str, dict[str, Any]]) -> float:
    return sum(
        float(row.get("marketValue") or 0)
        for row in positions.values()
        if row.get("entryChannel") == "weak_to_strong"
    )


def weak_to_strong_planned_value(orders: list[dict[str, Any]]) -> float:
    return sum(
        float(order.get("referencePrice") or 0) * int(order.get("shares") or 0)
        for order in orders
        if order.get("entryChannel") == "weak_to_strong"
    )


def mainline_probe_exposure_value(positions: dict[str, dict[str, Any]]) -> float:
    return sum(
        float(row.get("marketValue") or 0)
        for row in positions.values()
        if row.get("entryChannel") == "mainline_probe"
    )


def mainline_probe_planned_value(orders: list[dict[str, Any]]) -> float:
    return sum(
        float(order.get("referencePrice") or 0) * int(order.get("shares") or 0)
        for order in orders
        if order.get("entryChannel") == "mainline_probe"
    )


def update_missed_opportunities(
    old_rows: list[dict[str, Any]],
    preferred: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    missed_reasons: dict[str, list[str]],
    positions: dict[str, dict[str, Any]],
    latest_data_date: str | None,
    asset_version: str,
    market_policy: dict[str, Any],
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    by_key = {str(row.get("key")): dict(row) for row in old_rows if row.get("key")}
    pending_by_code = {row.get("code"): row for row in pending}
    held_codes = set(positions)
    for stock in preferred:
        code = stock.get("code")
        if not code or code in held_codes:
            continue
        reasons = missed_reasons.get(code) or list((pending_by_code.get(code) or {}).get("blockers") or ["not_selected"])
        price = positive_float(stock.get("currentClose"))
        key = f"{latest_data_date or 'unknown'}:{code}"
        row = by_key.get(key, {})
        first_price = positive_float(row.get("firstPrice")) or price
        gain = gain_from_prices(first_price, price)
        row.update(
            {
                "key": key,
                "date": latest_data_date,
                "code": code,
                "name": stock.get("name", code),
                "rank": stock.get("rank"),
                "score": stock.get("finalScore"),
                "minBuyScore": market_policy.get("minBuyScore"),
                "marketRegime": market_policy.get("regime"),
                "firstSeenAt": row.get("firstSeenAt") or stock.get("latestQuoteTime") or asset_version,
                "lastSeenAt": stock.get("latestQuoteTime") or asset_version,
                "firstPrice": first_price,
                "latestPrice": price,
                "gainPct": gain,
                "reasons": reasons,
                "primaryReason": primary_missed_reason(reasons),
                "reviewCadence": cfg.get("missedOpportunityReviewCadence") or "weekly",
            }
        )
        by_key[key] = row
    rows = sorted(by_key.values(), key=lambda row: (row.get("date") or "", numeric_float(row.get("gainPct")) or -999), reverse=True)
    return rows[:300]


def primary_missed_reason(reasons: list[str]) -> str:
    if not reasons:
        return "not_selected"
    priority = [
        "score_below_min",
        "risk_not_approved",
        "danger_anomaly",
        "suspended",
        "limit_up",
        "missing_price",
        "missing_score",
        "exposure_limit",
        "max_positions",
        "stale_quotes",
        "before_buy_window",
        "needs_confirmation",
    ]
    for reason in priority:
        if reason in reasons:
            return reason
    return reasons[0]


def build_missed_opportunity_summary(
    missed: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    latest_data_date: str | None,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    window_rows = [row for row in missed if in_recent_days(row.get("date"), latest_data_date, 7)]
    held_pnls = [float(row.get("pnlPct")) for row in positions.values() if row.get("pnlPct") is not None]
    held_avg = round(sum(held_pnls) / len(held_pnls), 2) if held_pnls else None
    gain_threshold = float(cfg.get("missedOpportunityGainThresholdPct") or 0)
    outperform_threshold = float(cfg.get("missedOpportunityOutperformThresholdPct") or 0)
    strong: list[dict[str, Any]] = []
    for row in window_rows:
        gain = numeric_float(row.get("gainPct"))
        if gain is None:
            continue
        outperformance = None if held_avg is None else round(gain - held_avg, 2)
        row["outperformancePct"] = outperformance
        if gain >= gain_threshold and (outperformance is None or outperformance >= outperform_threshold):
            strong.append(row)
    strong = sorted(strong, key=lambda row: numeric_float(row.get("gainPct")) or 0, reverse=True)
    return {
        "reviewCadence": cfg.get("missedOpportunityReviewCadence") or "weekly",
        "windowDays": 7,
        "latestDataDate": latest_data_date,
        "trackedCount": len(window_rows),
        "strongMissedCount": len(strong),
        "heldAveragePnlPct": held_avg,
        "gainThresholdPct": gain_threshold,
        "outperformThresholdPct": outperform_threshold,
        "topStrongMissed": strong[:8],
    }


def in_recent_days(row_date: object, latest_data_date: str | None, days: int) -> bool:
    if not row_date or not latest_data_date:
        return True
    try:
        left = datetime.strptime(str(row_date), "%Y-%m-%d")
        right = datetime.strptime(str(latest_data_date), "%Y-%m-%d")
    except ValueError:
        return True
    return 0 <= (right - left).days < days


def gain_from_prices(first_price: float | None, latest_price: float | None) -> float | None:
    if not first_price or latest_price is None:
        return None
    return round((latest_price / first_price - 1) * 100, 2)


def position_pnl_pct(position: dict[str, Any], price: float) -> float:
    avg_cost = positive_float(position.get("avgCost")) or positive_float(position.get("costBasis"))
    if not avg_cost:
        return 0.0
    return round((price / avg_cost - 1) * 100, 2)


def position_lot(shares: int, cost: float, trade_date: str | None, time: str | None) -> dict[str, Any]:
    return {
        "shares": int(shares),
        "costBasis": round(float(cost), 2),
        "tradeDate": normalize_trade_date(trade_date) or normalize_trade_date(time),
        "time": time,
    }


def ensure_position_lots(position: dict[str, Any]) -> list[dict[str, Any]]:
    lots = position.get("lots")
    if isinstance(lots, list) and lots:
        cleaned: list[dict[str, Any]] = []
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            shares = int(lot.get("shares") or 0)
            if shares <= 0:
                continue
            cleaned.append(
                {
                    "shares": shares,
                    "costBasis": round(float(lot.get("costBasis") or 0), 2),
                    "tradeDate": normalize_trade_date(lot.get("tradeDate")) or normalize_trade_date(lot.get("time")),
                    "time": lot.get("time"),
                }
            )
        if cleaned:
            position["lots"] = cleaned
            return cleaned
    shares = int(position.get("shares") or 0)
    if shares <= 0:
        position["lots"] = []
        return []
    lot = position_lot(
        shares,
        float(position.get("costBasis") or 0),
        normalize_trade_date(position.get("entryTradeDate")) or normalize_trade_date(position.get("entryTime")),
        position.get("entryTime"),
    )
    position["lots"] = [lot]
    return position["lots"]


def position_available_shares(position: dict[str, Any], current_trade_date: str | None) -> int:
    current_date = normalize_trade_date(current_trade_date)
    if not current_date:
        return int(position.get("shares") or 0)
    available = 0
    for lot in ensure_position_lots(position):
        trade_date = normalize_trade_date(lot.get("tradeDate")) or normalize_trade_date(lot.get("time"))
        if not trade_date or current_date > trade_date:
            available += int(lot.get("shares") or 0)
    return min(available, int(position.get("shares") or 0))


def annotate_lot_availability(position: dict[str, Any], current_trade_date: str | None) -> None:
    current_date = normalize_trade_date(current_trade_date)
    for lot in ensure_position_lots(position):
        trade_date = normalize_trade_date(lot.get("tradeDate")) or normalize_trade_date(lot.get("time"))
        lot["availableShares"] = int(lot.get("shares") or 0) if (not current_date or not trade_date or current_date > trade_date) else 0


def consume_position_lots(position: dict[str, Any], shares: int, current_trade_date: str | None) -> None:
    remaining_to_sell = int(shares)
    updated: list[dict[str, Any]] = []
    current_date = normalize_trade_date(current_trade_date)
    for lot in ensure_position_lots(position):
        lot_shares = int(lot.get("shares") or 0)
        if remaining_to_sell <= 0:
            updated.append(lot)
            continue
        trade_date = normalize_trade_date(lot.get("tradeDate")) or normalize_trade_date(lot.get("time"))
        sellable = not current_date or not trade_date or current_date > trade_date
        if not sellable:
            updated.append(lot)
            continue
        consumed = min(lot_shares, remaining_to_sell)
        remaining_to_sell -= consumed
        left = lot_shares - consumed
        if left > 0:
            ratio = left / lot_shares if lot_shares else 0
            lot = dict(lot)
            lot["shares"] = left
            lot["costBasis"] = round(float(lot.get("costBasis") or 0) * ratio, 2)
            updated.append(lot)
    position["lots"] = updated


def position_can_sell(position: dict[str, Any], current_trade_date: str | None) -> bool:
    return position_available_shares(position, current_trade_date) > 0


def fill_trade_date(fill: dict[str, Any]) -> str | None:
    return (
        normalize_trade_date(fill.get("time"))
        or normalize_trade_date(fill.get("assetVersion"))
        or normalize_trade_date(fill.get("tradeDate"))
    )


def execution_datetime(asset_version: object | None) -> datetime:
    return parse_quote_datetime(asset_version) or datetime.now()


def fill_execution_datetime(fill: dict[str, Any]) -> datetime | None:
    return parse_quote_datetime(fill.get("time")) or parse_quote_datetime(fill.get("assetVersion"))


def trade_date_from_datetime(value: datetime | None) -> str | None:
    return value.date().isoformat() if value is not None else None


def within_trade_execution_window(value: datetime | None, cfg: dict[str, Any]) -> bool:
    if value is None:
        return True
    windows = cfg.get("tradeExecutionWindows") or DEFAULT_CONFIG["tradeExecutionWindows"]
    current_minutes = value.hour * 60 + value.minute
    for start, end in windows:
        start_minutes = hhmm_to_minutes(str(start))
        end_minutes = hhmm_to_minutes(str(end))
        if start_minutes is None or end_minutes is None:
            continue
        if start_minutes <= current_minutes <= end_minutes:
            return True
    return False


def hhmm_to_minutes(value: str) -> int | None:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except ValueError:
        return None
    return hour * 60 + minute


def normalize_trade_date(value: object) -> str | None:
    text = str(value or "")
    digits = "".join(ch for ch in text[:10] if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def numeric_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round_lot(shares: int) -> int:
    return max(0, int(shares) // 100 * 100)


def event(kind: str, code: str, reason: str, message: str) -> dict[str, Any]:
    return {
        "time": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "code": code,
        "reason": reason,
        "message": message,
    }
