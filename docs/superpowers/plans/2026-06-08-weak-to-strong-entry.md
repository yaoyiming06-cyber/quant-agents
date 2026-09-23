# Weak-To-Strong Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cautious morning weak-to-strong simulated-buy path alongside the existing afternoon trend path.

**Architecture:** Persist fresh intraday observations in a separate reversal watch map, classify two consecutive recovery confirmations, and route eligible signals through a separately capped small-position buy path. Reuse existing order execution, quote guards, total exposure limits, and T+1 enforcement.

**Tech Stack:** Python, unittest, static HTML/CSS/JavaScript dashboard.

---

### Task 1: Signal Tests

**Files:**
- Modify: `tests/test_core.py`

- [ ] Add failing tests for two-confirmation morning entry, risk-off sizing, fake rebound rejection, and T+1 lock.
- [ ] Run the focused unittest methods and verify they fail for missing weak-to-strong behavior.

### Task 2: Reversal Watch And Classification

**Files:**
- Modify: `quant_agents/simulated_trading.py`

- [ ] Add weak-to-strong configuration defaults.
- [ ] Persist bounded fresh-quote observations in `weakToStrongWatch`.
- [ ] Classify baseline, confirming, confirmed, and blocked states.
- [ ] Expose weak-to-strong status in pending signals.
- [ ] Run focused tests and verify the classifier passes.

### Task 3: Small Morning Buy Path

**Files:**
- Modify: `quant_agents/simulated_trading.py`

- [ ] Route confirmed morning reversal signals into buy orders with reason `weak_to_strong_confirmed`.
- [ ] Apply regime-specific single-position and channel-total caps while retaining overall exposure rules.
- [ ] Preserve order metadata in fills and positions.
- [ ] Run focused tests and verify buys, sizing, and T+1 behavior.

### Task 4: Dashboard Labels

**Files:**
- Modify: `web_dashboard/app.js`
- Test: `tests/test_core.py`

- [ ] Add static UI assertions for weak-to-strong rule and reason labels.
- [ ] Add the weak-to-strong rule summary and readable order/fill reason.
- [ ] Run JavaScript syntax check and focused UI test.

### Task 5: Full Verification

**Files:**
- No production changes expected.

- [ ] Run the complete unittest suite.
- [ ] Rebuild dashboard data.
- [ ] Refresh `trading.html` and verify the new rule/status renders without changing existing afternoon behavior.
