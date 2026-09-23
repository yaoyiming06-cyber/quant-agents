# Pyramiding And Strategy B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add winning-position pyramiding to Strategy A and a lower-threshold Strategy B simulation with a dedicated dashboard page.

**Architecture:** Extend `quant_agents.simulated_trading` so the same engine can run Strategy A and Strategy B with different config and state paths. Add lot-level position tracking for T+1 correctness. Extend dashboard data generation to produce `simulatedTradingB` and a comparison payload, then render a new Strategy B page in the existing static dashboard.

**Tech Stack:** Python standard library, `unittest`, existing static HTML/CSS/JS dashboard.

---

### Task 1: Strategy Core Tests

**Files:**
- Modify: `tests/test_core.py`

- [ ] Add tests proving a profitable confirmed holding gets a pyramiding buy.
- [ ] Add tests proving a losing holding does not get averaged down.
- [ ] Add tests proving today's add-on lot remains T+1 locked while older lots stay sellable.
- [ ] Run the focused tests and confirm they fail before production changes.

### Task 2: Strategy Core Implementation

**Files:**
- Modify: `quant_agents/simulated_trading.py`

- [ ] Add default pyramiding config.
- [ ] Add lot migration, buy lot append, sellable-share calculation, and lot-aware sell consumption.
- [ ] Add pyramiding signal creation before new-name buys.
- [ ] Preserve the existing output shape while adding `lots`, `pyramidLevel`, `availableShares`, and add-on fill metadata.
- [ ] Run focused tests until green.

### Task 3: Strategy B Data Pipeline

**Files:**
- Modify: `tools/build_dashboard_data.py`
- Modify: `tests/test_core.py`

- [ ] Add a Strategy B config builder with lower score thresholds and start-equity seeding.
- [ ] Update dashboard generation to call `update_simulated_trading` for B using `data/features/simulated_trading_b.json`.
- [ ] Add `simulatedTradingB` and `strategySimulationComparison` to `window.QA_DATA`.
- [ ] Add tests for B config and A/B comparison payload.
- [ ] Run focused tests until green.

### Task 4: Strategy B Dashboard Page

**Files:**
- Add: `web_dashboard/strategy-b.html`
- Modify: `web_dashboard/app.js`
- Modify: `web_dashboard/styles.css`
- Modify: existing `web_dashboard/*.html` side navigation entries if needed.

- [ ] Add Strategy B link to navigation.
- [ ] Render B account metrics, positions, fills, pending signals, and A/B comparison.
- [ ] Add premium motion styles scoped to the Strategy B page.
- [ ] Run `node --check web_dashboard/app.js`.

### Task 5: Regenerate And Verify

**Files:**
- Generated: `web_dashboard/data.js`
- Generated: `data/features/simulated_trading_b.json`

- [ ] Run the full Python unit test suite.
- [ ] Run `node --check web_dashboard/app.js`.
- [ ] Rebuild dashboard data using the current run artifacts.
- [ ] Verify the new page loads from `http://127.0.0.1:8788/strategy-b.html` if the local server is running.
