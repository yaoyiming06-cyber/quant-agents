# Close Brief And Daily News Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visible sidebar access to the daily close brief and a daily news page that separates positive and negative news, while enriching the close brief with system status, trading actions, and reasons.

**Architecture:** Keep the existing static dashboard pattern. Add one HTML shell for `daily-news.html`, route it through `web_dashboard/app.js`, and keep `close-brief.html` as the generated post-close artifact from `tools/run_post_close_update.py`.

**Tech Stack:** Static HTML/CSS/JavaScript dashboard, Python report generation, `unittest` coverage in `tests/test_core.py`.

---

### Task 1: Sidebar Entries

**Files:**
- Modify: `web_dashboard/*.html`
- Test: `tests/test_core.py`

- [ ] **Step 1: Write the failing test**

```python
def test_sidebar_links_close_brief_and_daily_news(self) -> None:
    for page_name in ("index.html", "pool.html", "trading.html", "information.html"):
        html = Path("web_dashboard", page_name).read_text(encoding="utf-8")
        self.assertIn('href="./close-brief.html"', html)
        self.assertIn(">收盘简报</a>", html)
        self.assertIn('href="./daily-news.html"', html)
        self.assertIn(">每日新闻</a>", html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_core.CoreWorkflowTests.test_sidebar_links_close_brief_and_daily_news`
Expected: FAIL because the links are not in the sidebar yet.

- [ ] **Step 3: Write minimal implementation**

Add these links after `实盘` in each sidebar:

```html
<a href="./close-brief.html" data-nav="close-brief"><span class="nav-icon icon-book"></span>收盘简报</a>
<a href="./daily-news.html" data-nav="daily-news"><span class="nav-icon icon-alert"></span>每日新闻</a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_core.CoreWorkflowTests.test_sidebar_links_close_brief_and_daily_news`
Expected: PASS.

### Task 2: Daily News Page

**Files:**
- Create: `web_dashboard/daily-news.html`
- Modify: `web_dashboard/app.js`
- Test: `tests/test_core.py`

- [ ] **Step 1: Write the failing test**

```python
def test_daily_news_page_groups_positive_and_negative_news(self) -> None:
    page = Path("web_dashboard/daily-news.html").read_text(encoding="utf-8")
    app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")
    self.assertIn('data-page="daily-news"', page)
    self.assertIn("function renderDailyNews()", app_js)
    self.assertIn("dailyNewsRowsByPolarity", app_js)
    self.assertIn("利好", app_js)
    self.assertIn("利空", app_js)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_core.CoreWorkflowTests.test_daily_news_page_groups_positive_and_negative_news`
Expected: FAIL because the page and renderer do not exist.

- [ ] **Step 3: Write minimal implementation**

Create `daily-news.html` using the same shell as `information.html`; add `renderDailyNews()`, `dailyNewsRowsByPolarity()`, and route `page === "daily-news"` to the renderer.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_core.CoreWorkflowTests.test_daily_news_page_groups_positive_and_negative_news`
Expected: PASS.

### Task 3: Rich Close Brief

**Files:**
- Modify: `tools/run_post_close_update.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Write the failing test**

```python
def test_close_brief_includes_trade_actions_and_reasons(self) -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        dashboard = root / "web_dashboard"
        dashboard.mkdir()
        (dashboard / "data.js").write_text(
            "window.DASHBOARD_DATA = " + json.dumps({
                "automationStatus": {"postClose": {"status": "ok", "label": "盘后"}},
                "simulatedTrading": {
                    "latestFills": [{"status": "filled", "side": "buy", "code": "600522", "name": "中天科技", "reason": "confirmed_signal"}],
                    "orders": [{"side": "sell", "code": "000021", "name": "深科技", "reason": "sector_breakdown"}],
                    "pendingSignals": [{"code": "600584", "name": "长电科技", "blockers": ["score_below_min"]}],
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        with patch("tools.run_post_close_update.ROOT", root):
            run_post_close_update.write_close_brief(dashboard / "close-brief.html", {"status": "ok"}, {"decisions": []}, {})
        html = (dashboard / "close-brief.html").read_text(encoding="utf-8")
    self.assertIn("今日系统运行", html)
    self.assertIn("今日操作与原因", html)
    self.assertIn("中天科技", html)
    self.assertIn("confirmed_signal", html)
    self.assertIn("score_below_min", html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_core.CoreWorkflowTests.test_close_brief_includes_trade_actions_and_reasons`
Expected: FAIL because the current close brief omits action/reason sections.

- [ ] **Step 3: Write minimal implementation**

Read `web_dashboard/data.js` if present, extract `automationStatus`, `simulatedTrading.latestFills`, `orders`, and `pendingSignals`, then render a concise action/reason table.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_core.CoreWorkflowTests.test_close_brief_includes_trade_actions_and_reasons`
Expected: PASS.

### Task 4: Verification

- [ ] Run targeted tests:

```bash
.venv/bin/python -m unittest \
  tests.test_core.CoreWorkflowTests.test_sidebar_links_close_brief_and_daily_news \
  tests.test_core.CoreWorkflowTests.test_daily_news_page_groups_positive_and_negative_news \
  tests.test_core.CoreWorkflowTests.test_close_brief_includes_trade_actions_and_reasons
```

- [ ] Run broad checks:

```bash
.venv/bin/python -m unittest discover
node --check web_dashboard/app.js
.venv/bin/python -m compileall tools quant_agents
```
