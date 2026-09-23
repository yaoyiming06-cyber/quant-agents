# Strategy C Defensive Pool Correction Plan

> Supersedes the earlier mistaken plan that treated C as a buy/simulation strategy.

**Goal:** Add Strategy C as a weak-market defensive preferred-pool selection scheme, tracked beside A/B in the existing strategy comparison panel.

**Architecture:** C belongs to the preferred-pool layer. It must not create a separate simulated trading account, `simulatedTradingC`, `strategy-c.html`, or buy-execution config. The buy layer remains the main simulation plus the existing Strategy B shadow simulation.

**Correct implementation checklist:**

- [x] Add `build_defensive_strategy_candidates()` in `quant_agents/strategy_compare.py`.
- [x] Extend `strategyComparison` history to track `A`, `B`, and `C` candidates and outcomes.
- [x] Render the existing panel as `策略 A/B/C 跟踪`.
- [x] Keep Strategy B reachable only from the trading page action area, not the global sidebar.
- [x] Remove the mistaken C buy simulation and standalone C dashboard page.

**Verification:**

- [x] `.venv/bin/python -B -m unittest discover`
- [x] `node --check web_dashboard/app.js`
- [x] `.venv/bin/python -B tools/build_dashboard_data.py`
