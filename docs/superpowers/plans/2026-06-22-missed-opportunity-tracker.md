# Missed Opportunity Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the old full "未买入追踪" display as a richer "错过标的追踪" panel on the trading page.

**Architecture:** Keep the existing `missedOpportunities` and `missedOpportunitySummary` data contract. Replace the compact right-side miss card with a dedicated renderer that shows review summary, price path, gain after entry, relative holding performance, and blocker reasons.

**Tech Stack:** Static dashboard JavaScript/CSS, Python unittest, Node syntax check, local browser verification.

---

### Task 1: Lock The Full Tracker Contract

**Files:**
- Modify: `tests/test_core.py`

- [x] **Step 1: Write the failing test**

The trading dashboard test must assert these strings exist in the `renderTrading()` section:

```python
self.assertIn("missed-opportunity-tracker", trading)
self.assertIn("missedOpportunityTracker", trading)
self.assertIn("错过标的追踪", trading)
self.assertIn("复盘摘要", trading)
self.assertIn("未买原因", trading)
self.assertIn("入池后涨幅", trading)
self.assertIn("相对持仓", trading)
self.assertIn("价格轨迹", trading)
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -B -m unittest tests.test_core.CoreWorkflowTests.test_trading_dashboard_renders_global_sector_flow_leaders_and_hot_stocks
```

Expected: FAIL because the compact panel does not contain `missed-opportunity-tracker`.

### Task 2: Restore The Full Tracker UI

**Files:**
- Modify: `web_dashboard/app.js`
- Modify: `web_dashboard/styles.css`

- [ ] **Step 1: Add the tracker renderer**

Create `missedOpportunityTracker(rows, summary)` and `missedOpportunityRow(row)` beside `simMissedRow(row)`.

- [ ] **Step 2: Replace the compact card**

In `renderTrading()`, render `${missedOpportunityTracker(missedRows, missedSummary)}` before the automatic trading rules panel.

- [ ] **Step 3: Add CSS**

Style `.missed-opportunity-tracker`, `.missed-opportunity-summary`, `.missed-opportunity-row`, and `.missed-opportunity-flow`.

### Task 3: Verify

**Files:**
- Check: `web_dashboard/app.js`
- Check: `tests/test_core.py`

- [ ] **Step 1: Run focused test**

```bash
.venv/bin/python -B -m unittest tests.test_core.CoreWorkflowTests.test_trading_dashboard_renders_global_sector_flow_leaders_and_hot_stocks
```

- [ ] **Step 2: Run syntax check**

```bash
node --check web_dashboard/app.js
```

- [ ] **Step 3: Run full tests**

```bash
.venv/bin/python -B -m unittest discover
```

- [ ] **Step 4: Rebuild dashboard data**

```bash
.venv/bin/python -B tools/build_dashboard_data.py
```

- [ ] **Step 5: Browser verify**

Open `http://127.0.0.1:8788/trading.html` and confirm the visible page contains `错过标的追踪`, `复盘摘要`, and missed rows or the empty state.
