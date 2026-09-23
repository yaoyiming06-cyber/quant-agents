"""Run the lightweight A-share intraday dashboard refresh.

This script is intentionally deterministic so the cron automation can call one
entry point instead of relying on a long natural-language prompt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SHANGHAI = ZoneInfo("Asia/Shanghai")
RUN_WINDOWS = ((9, 35, 11, 25), (13, 5, 15, 20))
RUN_SLOTS = (
    "09:35",
    "09:50",
    "10:05",
    "10:20",
    "10:35",
    "10:50",
    "11:05",
    "11:20",
    "13:05",
    "13:20",
    "13:35",
    "13:50",
    "14:05",
    "14:20",
    "14:35",
    "14:50",
    "15:05",
)


class CommandFailed(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lightweight intraday update.")
    parser.add_argument("--date", type=str, default=None, help="Run date as YYYYMMDD; defaults to Asia/Shanghai today.")
    parser.add_argument("--force", action="store_true", help="Run even outside the intraday window.")
    parser.add_argument("--report", type=Path, default=None, help="Optional report JSON path.")
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Optional state JSON path used to avoid duplicate slot runs.",
    )
    parser.add_argument(
        "--slot-tolerance-minutes",
        type=int,
        default=12,
        help="Allow launchd jitter this many minutes after a scheduled slot.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    now = datetime.now(SHANGHAI)
    run_date = args.date or now.strftime("%Y%m%d")
    report_path = args.report or ROOT / "runs" / f"intraday_update_report_{run_date}.json"
    check_path = ROOT / "runs" / f"intraday_update_last_check_{run_date}.json"
    state_path = args.state or ROOT / "runs" / "intraday_update_state.json"
    slot = due_slot(now, args.slot_tolerance_minutes)

    report: dict[str, object] = {
        "run_date": run_date,
        "started_at": now.isoformat(timespec="seconds"),
        "forced": args.force,
        "within_window": in_run_window(now),
        "slot": slot,
        "commands": [],
    }

    if not args.force and (now.weekday() >= 5 or not in_run_window(now)):
        report["status"] = "skipped"
        report["reason"] = "outside_intraday_window"
        write_report(args.report or check_path, report)
        print_summary(report)
        return

    if not args.force and slot is None:
        report["status"] = "skipped"
        report["reason"] = "outside_intraday_slot"
        write_report(args.report or check_path, report)
        print_summary(report)
        return

    if not args.force and slot_already_completed(state_path, run_date, str(slot)):
        report["status"] = "skipped"
        report["reason"] = "slot_already_completed"
        write_report(args.report or check_path, report)
        print_summary(report)
        return

    state = load_json(state_path) if state_path.exists() else {}
    snapshot_path = runtime_file(state, run_date, "snapshot_path", "data/features/free_snapshots_*.json")
    evidence_path = runtime_file(state, run_date, "evidence_path", "data/evidence/institutional_evidence_*.json")
    expand_report_path = runtime_expand_report(state, run_date, snapshot_path)
    context_path = ROOT / "data" / "evidence" / f"context_evidence_preopen_{run_date}.json"
    if not context_path.exists():
        context_path = ROOT / "data" / "evidence" / f"context_evidence_intraday_{run_date}.json"
    plan_path = ROOT / "runs" / f"snapshot_plan_intraday_context_{run_date}.json"

    report.update(
        {
            "snapshot_path": str(snapshot_path.relative_to(ROOT)),
            "evidence_path": str(evidence_path.relative_to(ROOT)),
            "context_path": str(context_path.relative_to(ROOT)),
            "plan_path": str(plan_path.relative_to(ROOT)),
        }
    )

    try:
        run_command(
            report,
            [
                sys.executable,
                "-m",
                "quant_agents.cli",
                "collect-info-evidence",
                "--date",
                run_date,
                "--snapshots",
                str(snapshot_path),
                "--mode",
                "intraday",
                "--lookback-days",
                "1",
                "--max-news-per-code",
                "3",
                "--no-news",
                "--merge",
                "--out",
                str(context_path),
            ],
        )
        run_command(
            report,
            [
                sys.executable,
                "-m",
                "quant_agents.cli",
                "plan-from-snapshots",
                "--input",
                str(snapshot_path),
                "--evidence",
                str(evidence_path),
                "--context-evidence",
                str(context_path),
                "--force-rebalance",
                "--out",
                str(plan_path),
            ],
        )
        run_command(
            report,
            [
                sys.executable,
                "tools/build_dashboard_data.py",
                "--plan",
                str(plan_path),
                "--snapshot",
                str(snapshot_path),
                "--evidence",
                str(evidence_path),
                "--context-evidence",
                str(context_path),
                "--expand-report",
                str(expand_report_path),
                "--out",
                "web_dashboard/data.js",
                "--history",
                "data/features/preferred_pool_history.json",
                "--require-fresh-quotes",
                "--min-quote-fresh-ratio",
                "0.95",
                "--skip-archive-intraday",
            ],
        )
    except CommandFailed:
        report.setdefault("status", "error")
        report["finished_at"] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        write_report(report_path, report)
        print_summary(report)
        raise SystemExit(1)

    collect_report = load_json(ROOT / "runs" / f"info_evidence_collect_{run_date}_intraday.json")
    dashboard = load_dashboard_data(ROOT / "web_dashboard" / "data.js")
    report.update(
        {
            "status": "ok",
            "finished_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "quote_coverage": collect_report.get("stats", {}).get("quotes"),
            "theme_coverage": collect_report.get("stats", {}).get("themes"),
            "selected": [item.get("code") for item in dashboard.get("preferred", [])],
            "asset_version": dashboard.get("meta", {}).get("assetVersion"),
            "latest_quote_time": dashboard.get("meta", {}).get("latestQuoteTime"),
            "latest_index_quote_time": dashboard.get("meta", {}).get("latestIndexQuoteTime"),
            "website_url": "http://127.0.0.1:8788/index.html",
        }
    )
    previous_state = load_json(state_path) if state_path.exists() else {}
    previous_completed_slot = (
        previous_state.get("last_completed_slot")
        if previous_state.get("run_date") == run_date
        else None
    )
    state_payload = {
        "run_date": run_date,
        "last_completed_slot": slot if slot is not None and not args.force else previous_completed_slot,
        "finished_at": report["finished_at"],
        "asset_version": report["asset_version"],
        "latest_quote_time": report["latest_quote_time"],
        "snapshot_path": str(snapshot_path.relative_to(ROOT)),
        "evidence_path": str(evidence_path.relative_to(ROOT)),
        "expand_report_path": str(expand_report_path.relative_to(ROOT)),
    }
    write_state(state_path, state_payload)
    write_report(report_path, report)
    print_summary(report)


def in_run_window(now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    return any(start_h * 60 + start_m <= minutes <= end_h * 60 + end_m for start_h, start_m, end_h, end_m in RUN_WINDOWS)


def due_slot(now: datetime, tolerance_minutes: int) -> str | None:
    rounded_now = now.replace(second=0, microsecond=0)
    for slot in RUN_SLOTS:
        hour, minute = (int(part) for part in slot.split(":"))
        scheduled = rounded_now.replace(hour=hour, minute=minute)
        if scheduled <= now <= scheduled + timedelta(minutes=tolerance_minutes):
            return slot
    return None


def slot_already_completed(path: Path, run_date: str, slot: str) -> bool:
    state = load_json(path) if path.exists() else {}
    return state.get("run_date") == run_date and state.get("last_completed_slot") == slot


def latest_file(pattern: str) -> Path:
    from tools.runtime_files import latest_free_snapshot_file

    if pattern == "data/features/free_snapshots_*.json":
        return latest_free_snapshot_file(ROOT, pattern)
    files = sorted(ROOT.glob(pattern), key=runtime_file_sort_key, reverse=True)
    if not files:
        raise FileNotFoundError(pattern)
    return files[0]


def runtime_file_sort_key(path: Path) -> tuple[str, float]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    return (file_date_digits(path) or "", mtime)


def runtime_file(state: dict, run_date: str, state_key: str, pattern: str) -> Path:
    latest = latest_file(pattern)
    if state.get("run_date") == run_date and state.get(state_key):
        path = ROOT / str(state[state_key])
        if path.exists() and runtime_file_sort_key(path) >= runtime_file_sort_key(latest):
            return path
    return latest


def runtime_expand_report(state: dict, run_date: str, snapshot_path: Path) -> Path:
    if state.get("run_date") == run_date and state.get("expand_report_path"):
        path = ROOT / str(state["expand_report_path"])
        if path.exists():
            return path
    today_path = ROOT / "runs" / f"free_pool_expand_{run_date}.json"
    if today_path.exists():
        return today_path
    snapshot_date = file_date_digits(snapshot_path)
    if snapshot_date:
        snapshot_report = ROOT / "runs" / f"free_pool_expand_{snapshot_date}.json"
        if snapshot_report.exists():
            return snapshot_report
    return latest_file("runs/free_pool_expand_*.json")


def file_date_digits(path: Path) -> str | None:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[-8:]


def run_command(report: dict[str, object], command: list[str]) -> None:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    item = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }
    report.setdefault("commands", []).append(item)
    if completed.returncode != 0:
        report["status"] = "error"
        report["failed_command"] = command
        raise CommandFailed(json.dumps(report, ensure_ascii=False, indent=2))


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_dashboard_data(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text.removeprefix("window.QA_DATA = ").rstrip(";\n"))


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(report: dict[str, object]) -> None:
    print(f"status={report.get('status')}")
    if report.get("status") == "skipped":
        print(f"reason={report.get('reason')}")
        print(f"slot={report.get('slot')}")
        return
    if report.get("status") == "error":
        print(f"failed_command={report.get('failed_command')}")
        return
    print(f"quote_coverage={report.get('quote_coverage')}")
    print(f"theme_coverage={report.get('theme_coverage')}")
    print(f"selected={','.join(str(code) for code in report.get('selected', []))}")
    print(f"asset_version={report.get('asset_version')}")
    print(f"website_url={report.get('website_url')}")


if __name__ == "__main__":
    main()
