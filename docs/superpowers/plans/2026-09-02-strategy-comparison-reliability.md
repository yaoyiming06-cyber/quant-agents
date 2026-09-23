# Strategy Comparison Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make candidate performance reproducible, replace the independent B simulation with an A2 control account, and synchronize the dashboard without removing B candidate analysis.

**Architecture:** Keep A/B/C candidate generation in `strategy_compare.py` and make each historical candidate record store immutable selection metadata plus fixed 1/3/5/10/20 trading-day outcomes. Keep the existing simulation engine, seed a new A2 state once from A equity, and disable only `mainline_probe` in A2 through configuration. Build the dashboard from A and A2 while leaving B in `strategyComparison`.

**Tech Stack:** Python 3, `unittest`, JSON feature files, vanilla JavaScript dashboard.

---

### Task 1: Add failing tests for fixed candidate-period returns

**Files:**
- Modify: `/Users/a0000/.local/share/quant-agents/app/tests/test_core.py`
- Test target: `update_strategy_comparison()` in `/Users/a0000/.local/share/quant-agents/app/tools/build_dashboard_data.py`

- [ ] **Step 1: Write the failing test**

Add a test beside the existing strategy comparison tests:

```python
def test_strategy_comparison_freezes_target_period_returns(self) -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "strategy_comparison.json"
        update_strategy_comparison(
            path,
            "2026-06-01",
            "20260601132000",
            [{"code": "600522", "name": "中天科技", "rank": 1, "score": 0.80, "price": 40.0, "quoteTime": "20260601132000"}],
            [{"code": "600487", "name": "亨通光电", "rank": 1, "score": 0.82, "price": 76.0, "quoteTime": "20260601132000"}],
            [{"code": "600578", "name": "京能电力", "rank": 1, "score": 0.78, "price": 7.5, "quoteTime": "20260601132000"}],
            {},
        )
        first = update_strategy_comparison(
            path,
            "2026-06-05",
            "20260605150000",
            [],
            [],
            [],
            {
                "600522": {
                    "dailyCloses": {
                        "2026-06-02": 41.0,
                        "2026-06-04": 44.0,
                        "2026-06-05": 50.0,
                    },
                    "currentClose": 50.0,
                    "latestQuoteTime": "20260605150000",
                }
            },
        )
        second = update_strategy_comparison(
            path,
            "2026-06-06",
            "20260606150000",
            [],
            [],
            [],
            {
                "600522": {
                    "dailyCloses": {
                        "2026-06-02": 41.0,
                        "2026-06-04": 44.0,
                        "2026-06-05": 50.0,
                        "2026-06-06": 36.0,
                    },
                    "currentClose": 36.0,
                    "latestQuoteTime": "20260606150000",
                }
            },
        )

    row = next(item for item in second["history"] if item["date"] == "2026-06-01")["A"][0]
    self.assertEqual(2.5, row["return1d"])
    self.assertEqual(10.0, row["return3d"])
    self.assertNotEqual(36.0, row["return3d"])
    self.assertEqual(36.0, row["latestPrice"])
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_core.CoreWorkflowTests.test_strategy_comparison_freezes_target_period_returns -v
```

Expected: `FAIL` because the current strategy comparison records only a mutable `gainPct` and does not provide `return1d` or `return3d`.

### Task 2: Implement fixed candidate-period return storage

**Files:**
- Modify: `/Users/a0000/.local/share/quant-agents/app/tools/build_dashboard_data.py`
- Modify: `/Users/a0000/.local/share/quant-agents/app/tests/test_core.py`

- [ ] **Step 1: Add daily-close lookup to the latest-price input**

Keep `latestPriceByCode` as the existing map, but allow a row to carry a `dailyCloses` dictionary keyed by ISO trade date. Add these helpers near `update_candidate_outcome()`:

```python
TARGET_RETURN_PERIODS = (1, 3, 5, 10, 20)


def candidate_return_pct(entry_price: object, close: object) -> float | None:
    entry = _positive_float(entry_price)
    value = _positive_float(close)
    if entry is None or entry <= 0 or value is None:
        return None
    return round((value / entry - 1) * 100, 2)


def fixed_candidate_returns(row: dict[str, Any], latest: dict[str, Any]) -> dict[str, float]:
    daily_closes = latest.get("dailyCloses") if isinstance(latest.get("dailyCloses"), dict) else {}
    selection_date = first_text(row.get("selectionDate"), row.get("entryQuoteTime"), row.get("entryDate"))
    selection_day = selection_date[:10] if selection_date else ""
    dates = sorted(str(key)[:10] for key in daily_closes)
    future_dates = [day for day in dates if day > selection_day]
    result: dict[str, float] = {}
    for period in TARGET_RETURN_PERIODS:
        if len(future_dates) < period:
            continue
        value = candidate_return_pct(row.get("entryPrice"), daily_closes[future_dates[period - 1]])
        if value is not None:
            result[f"return{period}d"] = value
    return result
```

Use a real trading-date sequence when the available `dailyCloses` values include non-trading dates; the builder must pass the existing daily cache data into `latest_price_by_code` rather than infer dates from calendar arithmetic. If no daily close history is available, leave the fixed-period field absent.

- [ ] **Step 2: Make selection metadata immutable**

In `candidate_record()`, add:

```python
"selectionDate": first_text(row.get("selectionDate"), row.get("entryDate"), row.get("quoteTime"), row.get("entryQuoteTime")),
"selectionPrice": round(entry_price, 3) if entry_price is not None else None,
```

Keep `entryPrice` for compatibility. In `update_candidate_outcome()`, call `fixed_candidate_returns()` and set only missing `return{period}d` keys. Do not overwrite an existing fixed return on later refreshes.

- [ ] **Step 3: Change summaries to use fixed-period fields**

Replace the mutable `gainPct` aggregation in `summarize_outcomes()` and `comparison_summary()` with a period-aware summary:

```python
def summarize_outcomes(rows: list[dict[str, Any]], period: int = 1) -> dict[str, Any]:
    key = f"return{period}d"
    gains = [float(row[key]) for row in rows if row.get(key) is not None]
    if not gains:
        return {"period": period, "trackedCount": 0, "avgGainPct": None, "winRatePct": None, "best": None, "worst": None}
    best = max(rows, key=lambda row: float(row.get(key, -9999)))
    worst = min(rows, key=lambda row: float(row.get(key, 9999)))
    return {
        "period": period,
        "trackedCount": len(gains),
        "avgGainPct": round(mean(gains), 2),
        "winRatePct": round(sum(gain > 0 for gain in gains) / len(gains) * 100, 1),
        "best": compact_candidate(best, gain_key=key),
        "worst": compact_candidate(worst, gain_key=key),
    }
```

Preserve the latest/current `gainPct` only for current-status display. Store outcome summaries under `outcomes.A.periods`, `outcomes.B.periods`, and `outcomes.C.periods`; use the selected default period `1` for the existing `avgGainPct` compatibility field.

- [ ] **Step 4: Pass daily cache closes into candidate comparison**

When building `latest_price_by_code`, merge the existing daily cache rows for each code into a `dailyCloses` mapping. Keep the current quote fields unchanged. This makes the outcome calculation use real trading dates and avoids treating weekends as holding days.

- [ ] **Step 5: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_core.CoreWorkflowTests.test_strategy_comparison_freezes_target_period_returns tests.test_core.CoreWorkflowTests.test_strategy_comparison_preserves_first_same_day_candidates_and_entry_prices -v
```

Expected: both tests pass. Update older assertions only where they describe the old mutable `gainPct` summary contract; retain assertions for current `gainPct` display behavior.

### Task 3: Add failing tests for A2 initialization and configuration

**Files:**
- Modify: `/Users/a0000/.local/share/quant-agents/app/tests/test_core.py`
- Test targets: `/Users/a0000/.local/share/quant-agents/app/tools/build_dashboard_data.py` and `/Users/a0000/.local/share/quant-agents/app/quant_agents/simulated_trading.py`

- [ ] **Step 1: Write the failing tests**

Replace the old B-seeding test with:

```python
def test_seed_strategy_a2_state_uses_main_equity_once(self) -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "simulated_trading_a2.json"
        first = seed_strategy_a2_state(path, {"account": {"equity": 1_051_754.3}}, "2026-06-16")
        second = seed_strategy_a2_state(path, {"account": {"equity": 999_999.0}}, "2026-06-17")

    self.assertEqual("A2", first["strategy"])
    self.assertEqual(1_051_754.3, first["account"]["initialCash"])
    self.assertEqual("2026-06-16", first["startDate"])
    self.assertEqual(1_051_754.3, second["account"]["initialCash"])
    self.assertEqual("2026-06-16", second["startDate"])
```

Add:

```python
def test_strategy_a2_config_disables_mainline_probe(self) -> None:
    config = build_strategy_a2_config("2026-06-16")
    self.assertEqual("A2", config["strategyName"])
    self.assertFalse(config["mainlineProbeEnabled"])
    self.assertEqual("2026-06-16", config["startDate"])
```

Update the comparison test to call `build_strategy_simulation_comparison(main, a2)` and assert:

```python
self.assertEqual("A2", comparison["leaderByEquity"])
self.assertEqual(10_000, comparison["equityDiff"])
self.assertEqual("600584", comparison["latestA2Action"]["code"])
self.assertNotIn("B", comparison["ranked"][0]["strategy"])
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_core.CoreWorkflowTests.test_seed_strategy_a2_state_uses_main_equity_once tests.test_core.CoreWorkflowTests.test_strategy_a2_config_disables_mainline_probe -v
```

Expected: `FAIL` because the A2 builder functions do not exist.

### Task 4: Implement A2 simulation and remove B simulation runtime dependency

**Files:**
- Modify: `/Users/a0000/.local/share/quant-agents/app/tools/build_dashboard_data.py`
- Modify: `/Users/a0000/.local/share/quant-agents/app/quant_agents/simulated_trading.py` only if a reusable config guard is required
- Modify: `/Users/a0000/.local/share/quant-agents/app/tests/test_core.py`
- Create at runtime: `/Users/a0000/.local/share/quant-agents/app/data/features/simulated_trading_a2.json`
- Leave historical `/Users/a0000/.local/share/quant-agents/app/data/features/simulated_trading_b.json` untouched unless it is not needed by any active path

- [ ] **Step 1: Add A2 config and seed helpers**

Replace `build_strategy_b_config()` and `seed_strategy_b_state()` with:

```python
def build_strategy_a2_config(start_date: str | None = None) -> dict:
    return {
        "strategyName": "A2",
        "startDate": start_date,
        "mainlineProbeEnabled": False,
    }


def seed_strategy_a2_state(path: Path, main_simulated: dict, start_date: str | None) -> dict:
    return seed_strategy_state(
        path,
        main_simulated,
        start_date,
        "A2",
        "strategy_a2_simulation",
        "strategy_a2_start",
        "Strategy A2 control simulation seeded from Strategy A equity.",
    )
```

`seed_strategy_state()` must continue returning existing JSON unchanged when `path.exists()`, so both `startDate` and `initialCash` are retained.

- [ ] **Step 2: Use A2 in the dashboard builder**

Change the CLI path from `--sim-trading-b` to `--sim-trading-a2`, defaulting to `DEFAULT_SIMULATED_TRADING_A2_PATH`. Build A2 with:

```python
seed_strategy_a2_state(sim_trading_a2_path, simulated_trading, latest_data_date)
simulated_trading_a2 = update_simulated_trading(
    preferred,
    trade_ledger,
    sim_trading_a2_path,
    asset_version,
    latest_data_date,
    quote_quality,
    candidate_pool_quality=candidate_pool_quality,
    config=build_strategy_a2_config(),
    market_context=market_indices,
    execution_time=simulation_execution_time(latest_quote_time, asset_version),
)
```

Use `build_strategy_simulation_comparison(simulated_trading, simulated_trading_a2)` and change the comparison function's second label and deltas to A2. Add `A2` fields while preserving `A` fields:

```python
"A2": comparison_account_summary(account_a2),
"latestA2Action": latest_filled_action(strategy_a2),
```

Do not pass `latest_data_date` to the A2 config on every update; the seed file owns the immutable start date.

- [ ] **Step 3: Remove B simulation from chart markers and stock chart code collection**

Change the helpers to accept `simulated_trading_a2` and label markers as `A2`. The active dashboard data must no longer reference `simulatedTradingB` or `simulated_trading_b`. Keep `strategyComparison` construction with `build_shadow_strategy_candidates()` so B candidate analysis remains.

- [ ] **Step 4: Run simulation and comparison tests**

Run:

```bash
python3 -m unittest tests.test_core.CoreWorkflowTests.test_seed_strategy_a2_state_uses_main_equity_once tests.test_core.CoreWorkflowTests.test_strategy_a2_config_disables_mainline_probe tests.test_core.CoreWorkflowTests.test_strategy_simulation_comparison_compares_main_and_a2_accounts -v
```

Expected: all pass.

### Task 5: Add failing dashboard contract tests and update the web UI

**Files:**
- Modify: `/Users/a0000/.local/share/quant-agents/app/tests/test_core.py`
- Modify: `/Users/a0000/.local/share/quant-agents/app/tools/build_dashboard_data.py`
- Modify: `/Users/a0000/.local/share/quant-agents/app/web_dashboard/app.js`
- Modify: `/Users/a0000/.local/share/quant-agents/app/web_dashboard/trading.html`
- Delete: `/Users/a0000/.local/share/quant-agents/app/web_dashboard/strategy-b.html`
- Modify: `/Users/a0000/.local/share/quant-agents/app/web_dashboard/styles.css`

- [ ] **Step 1: Write the failing UI contract assertions**

Replace `test_strategy_b_dashboard_page_is_wired` with:

```python
def test_a2_comparison_replaces_b_simulation_page(self) -> None:
    app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")
    trading = Path("web_dashboard/trading.html").read_text(encoding="utf-8")
    build_script = Path("tools/build_dashboard_data.py").read_text(encoding="utf-8")

    self.assertIn("simulatedTradingA2", build_script)
    self.assertIn("simulatedTradingA2", app_js)
    self.assertIn("策略 A / A2 对照", app_js)
    self.assertIn("手续费", app_js)
    self.assertIn("滑点", app_js)
    self.assertNotIn("simulatedTradingB", build_script)
    self.assertNotIn("simulatedTradingB", app_js)
    self.assertNotIn("./strategy-b.html", trading)
    self.assertFalse(Path("web_dashboard/strategy-b.html").exists())
    self.assertIn("策略 A/B/C 跟踪", app_js)
```

- [ ] **Step 2: Run the UI test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_core.CoreWorkflowTests.test_a2_comparison_replaces_b_simulation_page -v
```

Expected: `FAIL` because the current dashboard still links to and reads the B simulation page/data.

- [ ] **Step 3: Replace the simulation page with an A2 comparison section**

Remove `renderStrategyB()`, `strategyBComparisonCard()`, `strategyBSignalRow()`, and B-only labels from `web_dashboard/app.js`. Keep `strategyComparisonPanel()` and the A/B/C candidate labels. Add the A2 comparison to `renderTrading()` using `DATA.simulatedTradingA2` and `DATA.strategySimulationComparison`, displaying:

```javascript
function strategySimulationComparisonCard(comparison) {
  const rows = comparison.ranked || [];
  return `
    <article class="panel strategy-simulation-comparison" data-hf>
      <div class="section-title-row">
        <h2>策略 A / A2 对照</h2>
        <span>${esc(comparison.leaderByEquity === "A2" ? "A2领先" : comparison.leaderByEquity === "A" ? "A领先" : "持平")}</span>
      </div>
      <div class="strategy-b-compare-grid">
        ${rows.map((row) => strategySimulationMetric(row)).join("")}
      </div>
      <div class="reason-list">
        <article class="reason-item">
          <span class="pill info">成本</span>
          <strong>手续费与税费、滑点均按各账户账本统计</strong>
          <p>用于判断关闭主线试仓后，收益变化是否来自交易成本和成交数量变化。</p>
        </article>
      </div>
    </article>
  `;
}
```

Use existing metric values from each account summary, with labels for equity, total PnL, fees/taxes, slippage, trade count, win rate, max drawdown, and exposure. Delete only CSS selectors whose sole active consumer is the removed B simulation page; keep selectors used by the A/B/C candidate panel or the A/A2 comparison.

- [ ] **Step 4: Remove the B page link and file**

Remove the `strategy-b.html` links from `renderTrading()` and `web_dashboard/trading.html`. Delete `web_dashboard/strategy-b.html`. Do not remove B candidate text from `strategyComparisonPanel()`.

- [ ] **Step 5: Update page dispatch and code-search helpers**

Remove `page === "strategy-b"` dispatches and remove A/B simulation page assumptions from `stock_chart_codes()` callers in `web_dashboard/app.js`. Keep current A simulation charts and add A2 markers through the same `markersByCode` path, with marker strategy `"A2"`.

- [ ] **Step 6: Run UI contract tests**

Run:

```bash
python3 -m unittest tests.test_core.CoreWorkflowTests.test_a2_comparison_replaces_b_simulation_page tests.test_core.CoreWorkflowTests.test_trading_dashboard_renders_global_sector_flow_leaders_and_hot_stocks -v
```

Expected: both pass, and the A/B/C candidate panel remains covered.

### Task 6: Rebuild the dashboard data and validate the full system

**Files:**
- Modify at runtime: `/Users/a0000/.local/share/quant-agents/app/data/features/strategy_comparison.json`
- Create or modify at runtime: `/Users/a0000/.local/share/quant-agents/app/data/features/simulated_trading_a2.json`
- Modify: `/Users/a0000/.local/share/quant-agents/app/web_dashboard/data.js`
- Modify generated HTML asset versions under `/Users/a0000/.local/share/quant-agents/app/web_dashboard/`

- [ ] **Step 1: Run the full Python test suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the dashboard builder with the existing project inputs**

Run the project's normal dashboard build command from the repository root:

```bash
python3 tools/build_dashboard_data.py
```

Expected: it writes `web_dashboard/data.js`, writes/updates `data/features/strategy_comparison.json`, creates `data/features/simulated_trading_a2.json`, and does not create new B simulation output.

- [ ] **Step 3: Verify active files and generated payload**

Run:

```bash
rg -n "simulatedTradingB|simulated_trading_b|strategy-b\\.html|renderStrategyB" tools web_dashboard/*.js web_dashboard/*.html tests
python3 - <<'PY'
import json
from pathlib import Path

text = Path("web_dashboard/data.js").read_text(encoding="utf-8")
payload = json.loads(text.split("=", 1)[1].rstrip(";\n"))
assert "simulatedTradingA2" in payload
assert "simulatedTradingB" not in payload
assert "B" in payload["strategyComparison"]["strategies"]
assert payload["strategySimulationComparison"]["ranked"]
print("dashboard payload checks passed")
PY
```

Expected: the search returns no active B simulation references in `tools`, current `web_dashboard/*.js`, current `web_dashboard/*.html`, or tests; the payload check prints `dashboard payload checks passed`; and `strategyComparison` still contains the B candidate strategy. Historical files under `web_dashboard/archive/` and the old `data/features/simulated_trading_b.json` may still contain old snapshots and are not active runtime dependencies.

- [ ] **Step 4: Check JavaScript syntax**

Run:

```bash
node --check web_dashboard/app.js
```

Expected: no output and exit code `0`.

- [ ] **Step 5: Report the final scope**

Report the changed active files, the generated A2 account start date and initial cash, the fixed candidate-period fields available in the rebuilt comparison data, and any remaining historical archive references. Do not claim that old archive snapshots have been rewritten.

No git commit step is included because `/Users/a0000/.local/share/quant-agents/app` is not a Git repository.
