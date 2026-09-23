"""Run the deterministic A-share post-close ranking update.

This script freezes the close snapshot/ranking and writes a close trading brief.
Pre-open evidence collection and live dashboard rebuilding happen in
``tools/run_preopen_check.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHANGHAI = ZoneInfo("Asia/Shanghai")
POST_CLOSE_START = (15, 10)


class CommandFailed(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run post-close data and plan update.")
    parser.add_argument("--date", type=str, default=None, help="Run date as YYYYMMDD; defaults to Asia/Shanghai today.")
    parser.add_argument("--force", action="store_true", help="Run even outside the post-close window.")
    parser.add_argument("--report", type=Path, default=None, help="Optional report JSON path.")
    parser.add_argument("--snapshot", type=Path, default=None, help="Optional snapshot file override.")
    parser.add_argument("--evidence", type=Path, default=None, help="Optional institutional evidence file override.")
    parser.add_argument("--plan", type=Path, default=None, help="Optional plan output path override.")
    parser.add_argument("--close-brief", type=Path, default=None, help="Optional close brief HTML output path.")
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--max-batches", type=int, default=80)
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument("--kline-source", choices=("eastmoney", "mootdx", "auto"), default="eastmoney")
    parser.add_argument(
        "--expand-timeout-seconds",
        type=int,
        default=2400,
        help="Treat expand-free-pool as a tolerated failure after this many seconds and continue with the latest snapshot.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    now = datetime.now(SHANGHAI)
    run_date = args.date or now.strftime("%Y%m%d")
    report_path = args.report or ROOT / "runs" / f"post_close_update_report_{run_date}.json"
    report: dict[str, object] = {
        "run_date": run_date,
        "started_at": now.isoformat(timespec="seconds"),
        "forced": args.force,
        "within_window": in_post_close_window(now),
        "commands": [],
    }

    if not args.force and not in_post_close_window(now):
        report["status"] = "skipped"
        report["reason"] = "outside_post_close_window"
        write_report(report_path, report)
        print_summary(report)
        return

    snapshot_path = resolve_output(args.snapshot, f"data/features/free_snapshots_{run_date}.json")
    target_snapshot_path = snapshot_path
    evidence_path = resolve_output(args.evidence, f"data/evidence/institutional_evidence_preopen_{run_date}.json")
    plan_path = resolve_output(args.plan, f"runs/snapshot_plan_close_{run_date}.json")
    close_brief_path = resolve_output(args.close_brief, "web_dashboard/close-brief.html")
    report.update(
        {
            "snapshot_path": display_path(snapshot_path),
            "evidence_path": display_path(evidence_path),
            "plan_path": display_path(plan_path),
            "close_brief_path": display_path(close_brief_path),
        }
    )
    completed_report = completed_post_close_report(
        report_path,
        required_outputs=[snapshot_path, plan_path, close_brief_path],
    )
    if completed_report is not None and not args.force:
        skipped_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        completed_report["last_skip_reason"] = "post_close_already_completed"
        completed_report["last_skipped_at"] = skipped_at
        completed_report["last_skip_check"] = {
            "started_at": report["started_at"],
            "required_outputs": [display_path(path) for path in (snapshot_path, plan_path, close_brief_path)],
        }
        write_report(report_path, completed_report)
        skip_report = dict(report)
        skip_report.update(
            {
                "status": "skipped",
                "reason": "post_close_already_completed",
                "previous_finished_at": completed_report.get("finished_at"),
                "finished_at": skipped_at,
            }
        )
        print_summary(skip_report)
        return

    try:
        expand_command = [
            sys.executable,
            "-m",
            "quant_agents.cli",
            "expand-free-pool",
            "--date",
            run_date,
            "--batch-size",
            str(args.batch_size),
            "--max-batches",
            str(args.max_batches),
            "--lookback-days",
            str(args.lookback_days),
            "--pause",
            str(args.pause),
            "--kline-source",
            args.kline_source,
            "--force-rebalance",
            "--out",
            str(target_snapshot_path),
            "--plan-out",
            str(plan_path),
        ]
        expand_result = run_command(
            report,
            expand_command,
            allow_failure=True,
            timeout_seconds=max(1, int(args.expand_timeout_seconds)),
        )
        expand_returncode = int(expand_result.get("returncode") or 0) if isinstance(expand_result, dict) else 0
        if expand_returncode != 0:
            fallback_snapshot = latest_existing_file("data/features/free_snapshots_*.json", exclude={target_snapshot_path})
            if fallback_snapshot is None:
                report["status"] = "error"
                report["reason"] = "expand_failed_no_snapshot_fallback"
                report["failed_command"] = expand_command
                raise CommandFailed(json.dumps(report, ensure_ascii=False, indent=2))
            snapshot_path = fallback_snapshot
            report.update(
                {
                    "status": "degraded",
                    "reason": "snapshot_fallback_used",
                    "snapshot_path": display_path(snapshot_path),
                    "target_snapshot_path": display_path(target_snapshot_path),
                    "snapshotFallback": {
                        "used": True,
                        "path": display_path(snapshot_path),
                        "targetPath": display_path(target_snapshot_path),
                        "reason": "expand-free-pool failed; continued post-close evidence and planning with latest available snapshot.",
                    },
                }
            )
        else:
            report["snapshotFallback"] = {"used": False}

    except CommandFailed:
        report.setdefault("status", "error")
        report["finished_at"] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        write_report(report_path, report)
        print_summary(report)
        raise SystemExit(1)

    try:
        if (report.get("snapshotFallback") or {}).get("used") and not plan_has_decisions(plan_path):
            build_fallback_plan(report, snapshot_path, evidence_path, plan_path)
    except CommandFailed:
        report.setdefault("status", "error")
        report["finished_at"] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        write_report(report_path, report)
        print_summary(report)
        raise SystemExit(1)

    expand_report_path = ROOT / "runs" / f"free_pool_expand_{run_date}.json"
    expand_report = load_json_if_available(expand_report_path)
    plan = load_json_if_available(plan_path)
    fallback_snapshot_count = None
    if not expand_report and snapshot_path.exists():
        snapshot_rows = load_json(snapshot_path)
        if isinstance(snapshot_rows, list):
            fallback_snapshot_count = len(snapshot_rows)
    selected_codes = [item.get("code") for item in plan.get("decisions", []) if item.get("code")]
    remaining = expand_report.get("remaining")
    pool_complete = expand_report.get("complete")
    pool_incomplete = pool_complete is False or (isinstance(remaining, int) and remaining > 0)
    status = "degraded" if report.get("status") == "degraded" or pool_incomplete else "ok"
    if pool_incomplete:
        report.setdefault("reason", "candidate_pool_incomplete")
    report.update(
        {
            "status": status,
            "finished_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "eligible_candidates": expand_report.get("eligible_candidates"),
            "snapshots": expand_report.get("snapshots", fallback_snapshot_count),
            "skipped": expand_report.get("skipped"),
            "remaining": remaining,
            "pool_complete": pool_complete,
            "universe_count": len(plan.get("universe", [])),
            "decision_count": len(plan.get("decisions", [])),
            "selected": selected_codes,
            "close_brief_path": display_path(close_brief_path),
            "website_url": "http://127.0.0.1:8788/close-brief.html",
        }
    )
    write_close_brief(close_brief_path, report, plan, expand_report)
    write_report(report_path, report)
    print_summary(report)


def in_post_close_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    start = POST_CLOSE_START[0] * 60 + POST_CLOSE_START[1]
    return minutes >= start


def resolve_output(path: Path | None, default: str) -> Path:
    if path is not None:
        return path if path.is_absolute() else ROOT / path
    return ROOT / default


def latest_existing_file(pattern: str, exclude: set[Path] | None = None) -> Path | None:
    from tools.runtime_files import latest_free_snapshot_file

    excluded = {path.resolve() for path in (exclude or set())}
    files = [
        path
        for path in ROOT.glob(pattern)
        if path.exists() and path.resolve() not in excluded
    ]
    if not files:
        return None
    if pattern == "data/features/free_snapshots_*.json":
        try:
            return latest_free_snapshot_file(ROOT, pattern, exclude=exclude)
        except FileNotFoundError:
            return None
    return max(files, key=lambda path: path.stat().st_mtime)


def completed_post_close_report(report_path: Path, required_outputs: list[Path]) -> dict[str, object] | None:
    if not report_path.exists():
        return None
    try:
        report = load_json(report_path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(report, dict) or report.get("status") != "ok":
        return None
    if any(not path.exists() for path in required_outputs):
        return None
    command_failed = any(
        int(item.get("returncode") or 0) != 0
        for item in report.get("commands", [])
        if isinstance(item, dict)
    )
    if command_failed:
        return None
    return report


def run_command(
    report: dict[str, object],
    command: list[str],
    allow_failure: bool = False,
    timeout_seconds: int | None = None,
) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        item = {
            "command": command,
            "returncode": -1,
            "stdout": (error.stdout or "")[-4000:] if isinstance(error.stdout, str) else "",
            "stderr": f"command timed out after {timeout_seconds}s",
            "timedOut": True,
        }
        if allow_failure:
            item["toleratedFailure"] = True
        report.setdefault("commands", []).append(item)
        if allow_failure:
            return item
        report["status"] = "error"
        report["failed_command"] = command
        raise CommandFailed(json.dumps(report, ensure_ascii=False, indent=2))
    item = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }
    if completed.returncode != 0 and allow_failure:
        item["toleratedFailure"] = True
    report.setdefault("commands", []).append(item)
    if completed.returncode != 0:
        if allow_failure:
            return item
        report["status"] = "error"
        report["failed_command"] = command
        raise CommandFailed(json.dumps(report, ensure_ascii=False, indent=2))
    return item


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_available(path: Path) -> dict:
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError, KeyError):
        return {}
    return data if isinstance(data, dict) else {}


def plan_has_decisions(path: Path) -> bool:
    plan = load_json_if_available(path)
    decisions = plan.get("decisions")
    return isinstance(decisions, list) and bool(decisions)


def build_fallback_plan(report: dict[str, object], snapshot_path: Path, evidence_path: Path, plan_path: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "quant_agents.cli",
        "plan-from-snapshots",
        "--input",
        str(snapshot_path),
        "--force-rebalance",
        "--out",
        str(plan_path),
    ]
    if evidence_path.exists():
        command.extend(["--evidence", str(evidence_path)])
    run_command(report, command)


def load_dashboard_data(path: Path | None = None) -> dict:
    data_path = path or ROOT / "web_dashboard" / "data.js"
    if not data_path.exists():
        return {}
    try:
        text = data_path.read_text(encoding="utf-8")
        payload = text.split("=", 1)[1].strip()
        if payload.endswith(";"):
            payload = payload[:-1].strip()
        data = json.loads(payload)
    except (IndexError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def report_trade_date(report: dict[str, object], dashboard: dict) -> str:
    value = report.get("run_date") or (dashboard.get("simulatedTrading") or {}).get("latestDataDate") or (dashboard.get("meta") or {}).get("latestSnapshotDate")
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def row_matches_trade_date(row: dict, trade_date: str) -> bool:
    if not trade_date:
        return True
    compact = trade_date.replace("-", "")
    for key in ("tradeDate", "time", "assetVersion"):
        value = str(row.get(key) or "")
        if value.startswith(trade_date) or value.startswith(compact):
            return True
    return False


def close_brief_action_rows(dashboard: dict, report: dict[str, object]) -> list[dict[str, object]]:
    sim = dashboard.get("simulatedTrading") if isinstance(dashboard.get("simulatedTrading"), dict) else {}
    trade_date = report_trade_date(report, dashboard)
    actions: list[dict[str, object]] = []
    for fill in sim.get("latestFills") or []:
        if not isinstance(fill, dict) or not row_matches_trade_date(fill, trade_date):
            continue
        actions.append(
            {
                "type": "成交" if fill.get("status") == "filled" else "拦截",
                "side": side_text(fill.get("side")),
                "code": fill.get("code"),
                "name": fill.get("name"),
                "status": action_status_text(fill.get("status")),
                "reason": trade_reason_text(fill.get("reason") or fill.get("originalReason") or ""),
                "time": fill.get("time") or fill.get("assetVersion") or "",
            }
        )
    for order in sim.get("orders") or []:
        if not isinstance(order, dict) or not row_matches_trade_date(order, trade_date):
            continue
        actions.append(
            {
                "type": "委托",
                "side": side_text(order.get("side")),
                "code": order.get("code"),
                "name": order.get("name"),
                "status": action_status_text(order.get("status") or "pending"),
                "reason": trade_reason_text(order.get("reason") or ""),
                "time": order.get("time") or order.get("assetVersion") or "",
            }
        )
    for signal in (sim.get("pendingSignals") or [])[:8]:
        if not isinstance(signal, dict):
            continue
        blockers = signal.get("blockers") if isinstance(signal.get("blockers"), list) else []
        reasons = signal.get("reasons") if isinstance(signal.get("reasons"), list) else []
        actions.append(
            {
                "type": "未买入",
                "side": "观察",
                "code": signal.get("code"),
                "name": signal.get("name"),
                "status": "已拦截" if blockers else "等待确认",
                "reason": " / ".join(blocker_reason_text(item) for item in blockers or reasons or ["等待确认"]),
                "time": signal.get("quoteTime") or "",
            }
        )
    return actions[:24]


AUTOMATION_SEVERITY = {"error": 4, "missing": 3, "degraded": 2, "skipped": 1, "ok": 0}
AUTOMATION_STATUS_LABELS = {
    "ok": "正常",
    "degraded": "降级完成",
    "skipped": "跳过",
    "error": "失败",
    "missing": "未运行",
}


def close_brief_post_close_row(report: dict[str, object]) -> dict[str, object]:
    status = str(report.get("status") or "missing")
    remaining = report.get("remaining")
    if status == "ok" and (report.get("pool_complete") is False or (isinstance(remaining, int) and remaining > 0)):
        status = "degraded"
    if status not in AUTOMATION_STATUS_LABELS:
        status = "error"
    run_date = str(report.get("run_date") or datetime.now(SHANGHAI).strftime("%Y%m%d"))
    note = report.get("reason") or report.get("finished_at") or "报告已生成"
    return {
        "key": "post_close",
        "label": "下午收盘",
        "status": status,
        "statusText": AUTOMATION_STATUS_LABELS[status],
        "note": str(note),
        "finishedAt": report.get("finished_at"),
        "reportPath": display_path(ROOT / "runs" / f"post_close_update_report_{run_date}.json"),
    }


def close_brief_automation_rows(dashboard: dict, current_post_close_report: dict[str, object] | None = None) -> list[dict[str, object]]:
    automation = dashboard.get("automationStatus") if isinstance(dashboard.get("automationStatus"), dict) else {}
    items = automation.get("items") if isinstance(automation.get("items"), list) else []
    if items:
        rows = [dict(item) for item in items if isinstance(item, dict)]
    else:
        rows = []
        for key, item in automation.items():
            if isinstance(item, dict):
                rows.append({"key": key, **item})
    if current_post_close_report:
        post_close_row = close_brief_post_close_row(current_post_close_report)
        for index, row in enumerate(rows):
            if row.get("key") == "post_close":
                rows[index] = post_close_row
                break
        else:
            rows.append(post_close_row)
    return rows


def close_brief_automation_summary_status(rows: list[dict[str, object]], dashboard: dict) -> str:
    if rows:
        worst = max(rows, key=lambda item: AUTOMATION_SEVERITY.get(str(item.get("status") or "missing"), 4))
        status = str(worst.get("status") or "missing")
        return status if status in AUTOMATION_STATUS_LABELS else "error"
    automation = dashboard.get("automationStatus") if isinstance(dashboard.get("automationStatus"), dict) else {}
    status = str(automation.get("summaryStatus") or "missing")
    return status if status in AUTOMATION_STATUS_LABELS else "error"


def dashboard_stock_name_map(dashboard: dict) -> dict[str, str]:
    names: dict[str, str] = {}
    for key in ("preferred", "pool", "history"):
        rows = dashboard.get(key) if isinstance(dashboard.get(key), list) else []
        for row in rows:
            if isinstance(row, dict) and row.get("code") and row.get("name"):
                names[str(row["code"])] = str(row["name"])
    sim = dashboard.get("simulatedTrading") if isinstance(dashboard.get("simulatedTrading"), dict) else {}
    for key in ("positions", "fills", "latestFills", "orders", "pendingSignals"):
        rows = sim.get(key) if isinstance(sim.get(key), list) else []
        for row in rows:
            if isinstance(row, dict) and row.get("code") and row.get("name"):
                names.setdefault(str(row["code"]), str(row["name"]))
    return names


def snapshot_stock_name_map(report: dict[str, object]) -> dict[str, str]:
    raw_path = str(report.get("snapshot_path") or "")
    if not raw_path:
        return {}
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}
    names: dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("code") and row.get("name"):
            names[str(row["code"])] = str(row["name"])
    return names


def close_brief_decision_rows(plan: dict, name_map: dict[str, str] | None = None) -> list[dict[str, object]]:
    decisions = plan.get("decisions", []) if isinstance(plan.get("decisions"), list) else []
    names = name_map or {}
    rows = []
    for index, item in enumerate(decisions[:20], start=1):
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        rows.append(
            {
                "rank": index,
                "code": code,
                "name": item.get("name") or item.get("stock_name") or names.get(code) or code,
                "score": round(float(item.get("final_score") or item.get("score") or 0), 4),
                "risk": risk_text(item.get("risk_check") or item.get("riskCheck") or ""),
                "reason": decision_reason_summary(item.get("reason") or ""),
            }
        )
    return rows


def close_brief_payload(report: dict[str, object], plan: dict, expand_report: dict, dashboard: dict) -> dict[str, object]:
    names = snapshot_stock_name_map(report)
    names.update(dashboard_stock_name_map(dashboard))
    decisions = close_brief_decision_rows(plan, names)
    coverage_snapshots = expand_report.get("snapshots") or report.get("snapshots") or 0
    coverage_total = expand_report.get("eligible_candidates") or report.get("eligible_candidates") or 0
    automation_rows = close_brief_automation_rows(dashboard, report)
    automation_status = close_brief_automation_summary_status(automation_rows, dashboard)
    return {
        "title": "今日收盘交易简报",
        "subtitle": "收盘后固化全市场快照评分、优选池排序和当天模拟交易结果；盘前再吸收新闻、龙虎榜、美股夜盘等交易前信息。",
        "summary": {
            "status": AUTOMATION_STATUS_LABELS.get(automation_status, "未知"),
            "automationStatus": automation_status,
            "postCloseStatus": report_status_text(report.get("status") or "unknown"),
            "coverage": f"{coverage_snapshots}/{coverage_total}",
            "remaining": report.get("remaining") or 0,
            "decisionCount": len(decisions),
        },
        "automationRows": automation_rows,
        "actionRows": close_brief_action_rows(dashboard, report),
        "decisionRows": decisions,
    }


def write_close_brief(path: Path, report: dict[str, object], plan: dict, expand_report: dict) -> None:
    dashboard = load_dashboard_data()
    payload = close_brief_payload(report, plan, expand_report, dashboard)
    version = (dashboard.get("meta") or {}).get("assetVersion") or (dashboard.get("meta") or {}).get("generatedAt") or "local"
    html = f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>今日收盘交易简报 - A's decision</title>
    <link rel="stylesheet" href="./styles.css?v={html_escape(version)}">
    <script src="./data.js?v={html_escape(version)}"></script>
    <script>window.CLOSE_BRIEF_DATA = {json_for_script(payload)};</script>
    <script src="./app.js?v={html_escape(version)}" defer></script>
  </head>
  <body data-page="close-brief">
    <div class="app-shell">
      <header class="topbar">
        <a class="brand" href="./index.html" aria-label="A's decision">
          <span class="brand-mark" aria-hidden="true"><i></i></span>
          <span>A's decision</span>
        </a>
        <div class="top-search" role="search">
          <span class="search-dot" aria-hidden="true"></span>
          <input id="globalSearch" type="search" placeholder="搜索代码、名称、原因">
        </div>
        <div class="top-status">
          <span id="dataStatus">数据加载中</span>
        </div>
      </header>

      <aside class="side-nav" aria-label="主导航">
        <a href="./index.html" data-nav="dashboard"><span class="nav-icon icon-eyes"></span>总览</a>
        <a href="./pool.html" data-nav="pool"><span class="nav-icon icon-pool"></span>优选池</a>
        <a href="./history.html" data-nav="history"><span class="nav-icon icon-book"></span>历史</a>
        <a href="./anomalies.html" data-nav="anomalies"><span class="nav-icon icon-alert"></span>异常</a>
        <a href="./agents.html" data-nav="agents"><span class="nav-icon icon-gpt"></span>Agent</a>
        <a href="./trading.html" data-nav="trading"><span class="nav-icon icon-person"></span>实盘</a>
        <a href="./close-brief.html" data-nav="close-brief"><span class="nav-icon icon-book"></span>收盘简报</a>
        <a href="./daily-news.html" data-nav="daily-news"><span class="nav-icon icon-alert"></span>每日新闻</a>
      </aside>

      <main id="app" class="content" tabindex="-1">
        <section class="page-head">
          <div>
            <p class="eyebrow">A's decision</p>
            <h1>今日收盘交易简报</h1>
            <p class="subtle">今日系统运行、今日操作与原因、收盘优选池排序正在加载。</p>
          </div>
        </section>
      </main>
    </div>

    <div id="drawerBackdrop" class="drawer-backdrop" hidden></div>
    <aside id="detailDrawer" class="detail-drawer" aria-live="polite" aria-label="详情"></aside>
  </body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, html)


def trade_reason_text(value: object) -> str:
    return {
        "confirmed_signal": "行情确认",
        "weak_to_strong_confirmed": "弱转强确认",
        "mainline_probe": "主线试仓",
        "pyramid_add": "阶梯加仓",
        "market_de_risk": "大盘降仓",
        "out_of_pool": "退出优选池",
        "out_of_pool_confirmed": "出池确认",
        "sector_breakdown": "板块破位",
        "sector_breakdown_trim": "板块破位减仓",
        "sector_cooling": "板块降温",
        "sector_cooling_trim": "板块降温减仓",
        "stop_loss": "止损",
        "take_profit_trim": "止盈减半",
        "outside_trade_execution_window": "非交易时段",
        "outside_trade_execution_window_after_repair": "非交易时段修正",
        "before_buy_window_after_repair": "早盘买入修正",
        "max_positions_after_t_plus_one_repair": "仓位上限修正",
        "no_position_after_t_plus_one_repair": "无持仓修正",
        "t_plus_one_locked": "T+1 锁定",
        "cash_not_enough": "现金不足",
        "no_position": "无持仓",
    }.get(str(value or ""), str(value or "自动规则"))


def side_text(value: object) -> str:
    return {
        "buy": "买入",
        "sell": "卖出",
        "watch": "观察",
    }.get(str(value or ""), str(value or ""))


def action_status_text(value: object) -> str:
    return {
        "filled": "已成交",
        "rejected": "已拒绝",
        "pending": "待处理",
        "blocked": "已拦截",
        "waiting": "等待确认",
    }.get(str(value or ""), str(value or ""))


def report_status_text(value: object) -> str:
    return {
        "ok": "正常",
        "degraded": "降级完成",
        "skipped": "跳过",
        "error": "失败",
        "unknown": "未知",
    }.get(str(value or ""), str(value or ""))


def blocker_reason_text(value: object) -> str:
    return {
        "stale_quotes": "报价过期",
        "outside_trade_execution_window": "非交易时段",
        "needs_confirmation": "等待行情确认",
        "before_buy_window": "下午开仓规则限制",
        "score_below_min": "评分未过线",
        "missing_score": "缺少评分",
        "exposure_limit": "总仓已满",
        "max_positions": "持仓数量上限",
        "lot_size_or_cash": "现金/整手不足",
        "risk_not_approved": "风控未通过",
        "suspended": "停牌",
        "limit_up": "涨停不追",
        "limit_down": "跌停破位",
        "severe_intraday_drop": "盘中大跌",
        "sector_breakdown": "板块破位",
        "sector_flow_outflow": "主线资金流出",
        "danger_anomaly": "重大异常",
        "not_mainline": "非资金主线",
        "mainline_score_too_low": "主线评分不足",
        "weak_to_strong_needs_confirmation": "弱转强等待确认",
        "weak_to_strong_window_closed": "弱转强窗口结束",
        "等待确认": "等待确认",
    }.get(str(value or ""), str(value or "待复核"))


def risk_text(value: object) -> str:
    return {
        "approved": "风控通过",
        "rejected": "风控拒绝",
        "blocked": "风控拦截",
    }.get(str(value or ""), str(value or "待复核"))


def decision_reason_summary(reason: object) -> str:
    text = str(reason or "")
    labels: list[str] = []
    if "avg_turnover" in text or "turnover_rate" in text:
        labels.append("量能活跃")
    if "trend_quality=1" in text or "trend_quality=1.00" in text:
        labels.append("趋势质量高")
    if "no_material_event" in text or "neutral_news" in text:
        labels.append("信息面无重大风险")
    if "factor_trend" in text or "institutional:" in text:
        labels.append("机构/量化痕迹正常")
    if "relative_strength" in text:
        labels.append("相对强度可跟踪")
    if not labels:
        labels.append("综合评分靠前，等待盘前信息面复核")
    return "、".join(dict.fromkeys(labels))


def json_for_script(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def html_escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def write_text_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def print_summary(report: dict[str, object]) -> None:
    print(f"status={report.get('status')}")
    if report.get("status") == "skipped":
        print(f"reason={report.get('reason')}")
        return
    if report.get("status") == "error":
        print(f"failed_command={report.get('failed_command')}")
        return
    print(f"snapshots={report.get('snapshots')}")
    print(f"eligible_candidates={report.get('eligible_candidates')}")
    print(f"remaining={report.get('remaining')}")
    print(f"decisions={report.get('decision_count')}")
    print(f"selected={','.join(str(code) for code in report.get('selected', []))}")
    print(f"close_brief={report.get('close_brief_path')}")


if __name__ == "__main__":
    main()
