"""Run the deterministic A-share pre-open preview checks.

This script absorbs overnight information before the first intraday trading
slot, rebuilds the pre-open preferred pool, and refreshes the local dashboard.
"""

from __future__ import annotations

import argparse
import atexit
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
PREOPEN_START = (8, 30)
PREOPEN_END = (9, 25)


class CommandFailed(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pre-open information check previews.")
    parser.add_argument("--date", type=str, default=None, help="Run date as YYYYMMDD; defaults to Asia/Shanghai today.")
    parser.add_argument("--force", action="store_true", help="Run even outside the pre-open window.")
    parser.add_argument("--report", type=Path, default=None, help="Optional report JSON path.")
    parser.add_argument("--snapshot", type=Path, default=None, help="Optional snapshot file override.")
    parser.add_argument("--evidence", type=Path, default=None, help="Optional institutional evidence file override.")
    parser.add_argument("--institutional-timeout-seconds", type=int, default=480, help="Max seconds for institutional evidence collection.")
    parser.add_argument("--info-timeout-seconds", type=int, default=180, help="Max seconds for focused pre-open news collection.")
    parser.add_argument("--info-base-timeout-seconds", type=int, default=240, help="Max seconds for all-symbol quote/theme context collection.")
    parser.add_argument("--news-focus-limit", type=int, default=12, help="Max focus symbols for slower pre-open news collection.")
    parser.add_argument("--overnight-timeout-seconds", type=int, default=180, help="Max seconds for overnight market context collection.")
    parser.add_argument("--plan-timeout-seconds", type=int, default=240, help="Max seconds for each pre-open ranking rebuild.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    now = datetime.now(SHANGHAI)
    run_date = args.date or now.strftime("%Y%m%d")
    report_path = args.report or ROOT / "runs" / f"preopen_check_report_{run_date}.json"
    report: dict[str, object] = {
        "run_date": run_date,
        "started_at": now.isoformat(timespec="seconds"),
        "forced": args.force,
        "within_window": in_preopen_window(now),
        "commands": [],
    }

    if not args.force and not in_preopen_window(now):
        report["status"] = "skipped"
        report["reason"] = "skipped_due_late_trigger"
        write_report(report_path, report)
        print_summary(report)
        return

    lock_handle = acquire_run_lock(ROOT / "runs" / "preopen_check.lock")
    if lock_handle is None:
        report["status"] = "skipped"
        report["reason"] = "preopen_already_running"
        write_report(report_path, report)
        print_summary(report)
        return
    atexit.register(lock_handle.close)

    snapshot_path = resolve_runtime_file(args.snapshot, "data/features/free_snapshots_*.json")
    snapshot_trade_date = snapshot_trade_date_text(snapshot_path)
    evidence_path = resolve_output(args.evidence, f"data/evidence/institutional_evidence_preopen_{run_date}.json")
    context_path = ROOT / "data" / "evidence" / f"context_evidence_preopen_{run_date}.json"
    overnight_context_path = ROOT / "data" / "evidence" / f"overnight_market_context_{run_date}.json"
    baseline_plan_path = ROOT / "runs" / f"snapshot_plan_preopen_baseline_{run_date}.json"
    context_plan_path = ROOT / "runs" / f"snapshot_plan_preopen_context_{run_date}.json"
    expand_report_path = expand_report_for_snapshot(snapshot_path, snapshot_trade_date)
    report.update(
        {
            "snapshot_path": display_path(snapshot_path),
            "evidence_path": display_path(evidence_path),
            "context_path": display_path(context_path),
            "overnight_context_path": display_path(overnight_context_path),
            "baseline_plan_path": display_path(baseline_plan_path),
            "context_plan_path": display_path(context_plan_path),
            "expand_report_path": display_path(expand_report_path),
        }
    )

    try:
        institutional_item = run_command(
            report,
            [
                sys.executable,
                "-m",
                "quant_agents.cli",
                "collect-institutional-evidence",
                "--date",
                snapshot_trade_date,
                "--snapshots",
                str(snapshot_path),
                "--out",
                str(evidence_path),
                "--max-margin-symbols",
                "0",
                "--merge",
            ],
            allow_failure=True,
            timeout_seconds=max(1, int(args.institutional_timeout_seconds)),
        )
        if institutional_item.get("returncode") != 0:
            ensure_json_file(evidence_path, {})
        info_base_item = run_command(
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
                "pre-open",
                "--lookback-days",
                "1",
                "--max-news-per-code",
                "0",
                "--no-news",
                "--merge",
                "--out",
                str(context_path),
            ],
            allow_failure=True,
            timeout_seconds=max(1, int(args.info_base_timeout_seconds)),
        )
        if info_base_item.get("returncode") != 0:
            ensure_json_file(context_path, {})
        focus_codes = preopen_focus_codes(snapshot_path, max_codes=max(1, int(args.news_focus_limit)))
        if focus_codes:
            info_news_item = run_command(
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
                    "--codes",
                    ",".join(focus_codes),
                    "--mode",
                    "pre-open",
                    "--lookback-days",
                    "3",
                    "--max-news-per-code",
                    "6",
                    "--global-news-only",
                    "--no-llm",
                    "--no-quotes",
                    "--no-theme",
                    "--merge",
                    "--out",
                    str(context_path),
                ],
                allow_failure=True,
                timeout_seconds=max(1, int(args.info_timeout_seconds)),
            )
            if info_news_item.get("returncode") != 0:
                ensure_json_file(context_path, {})
        run_command(
            report,
            [
                sys.executable,
                "tools/collect_overnight_market_context.py",
                "--date",
                run_date,
                "--context-evidence",
                str(context_path),
                "--out",
                str(overnight_context_path),
                "--merge-context",
            ],
            allow_failure=True,
            timeout_seconds=max(1, int(args.overnight_timeout_seconds)),
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
                "--force-rebalance",
                "--out",
                str(baseline_plan_path),
            ],
            timeout_seconds=max(1, int(args.plan_timeout_seconds)),
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
                str(context_plan_path),
            ],
            timeout_seconds=max(1, int(args.plan_timeout_seconds)),
        )
    except CommandFailed:
        report.setdefault("status", "error")
        report["finished_at"] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        write_report(report_path, report)
        print_summary(report)
        raise SystemExit(1)

    context_evidence = load_json_if_available(context_path)
    context_metrics = context_evidence_metrics(context_evidence)
    institutional_report = load_json_if_available(ROOT / "runs" / f"institutional_evidence_collect_{snapshot_trade_date}.json")
    overnight_report = load_json_if_available(overnight_context_path)
    baseline_plan = load_json(baseline_plan_path)
    context_plan = load_json(context_plan_path)
    tolerated_failures = [
        item
        for item in report.get("commands", [])
        if isinstance(item, dict) and int(item.get("returncode") or 0) != 0 and item.get("toleratedFailure")
    ]
    blocking_tolerated_failure = any(not is_deferred_news_failure(item) for item in tolerated_failures)
    news_deferred = any(is_deferred_news_failure(item) for item in tolerated_failures)
    tolerated_failure = any(
        int(item.get("returncode") or 0) != 0 and item.get("toleratedFailure")
        for item in report.get("commands", [])
        if isinstance(item, dict)
    )
    report.update(
        {
            "status": "degraded" if blocking_tolerated_failure or overnight_report.get("status") == "degraded" else "ok",
            "finished_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "news_status": "deferred" if news_deferred else "ok",
            "institutional_evidence_codes": len(institutional_report.get("evidence_codes", [])),
            "context_evidence_codes": context_metrics["codes"],
            "overnight_status": overnight_report.get("status"),
            "overnight_sectors": len(overnight_report.get("sectors", [])),
            "quote_coverage": context_metrics["quotes"],
            "theme_coverage": context_metrics["themes"],
            "news_events": context_metrics["news_events"],
            "baseline_selected": [item.get("code") for item in baseline_plan.get("decisions", [])],
            "selected": [item.get("code") for item in context_plan.get("decisions", [])],
        }
    )
    write_report(report_path, report)
    run_command(
        report,
        [
            sys.executable,
            "tools/build_dashboard_data.py",
            "--plan",
            str(context_plan_path),
            "--snapshot",
            str(snapshot_path),
            "--evidence",
            str(evidence_path),
            "--context-evidence",
            str(context_path),
            "--expand-report",
            str(expand_report_path),
            "--out",
            str(ROOT / "web_dashboard" / "data.js"),
            "--automation-date",
            run_date_iso(run_date),
            "--skip-archive-intraday",
        ],
        timeout_seconds=max(1, int(args.plan_timeout_seconds)),
    )
    dashboard = load_dashboard_data(ROOT / "web_dashboard" / "data.js")
    report.update(
        {
            "asset_version": (dashboard.get("meta") or {}).get("assetVersion"),
            "website_url": "http://127.0.0.1:8788/index.html",
        }
    )
    write_report(report_path, report)
    print_summary(report)


def is_deferred_news_failure(item: dict) -> bool:
    command = [str(part) for part in item.get("command", [])]
    return (
        "collect-info-evidence" in command
        and "--global-news-only" in command
        and item.get("toleratedFailure")
    )


def in_preopen_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    start = PREOPEN_START[0] * 60 + PREOPEN_START[1]
    end = PREOPEN_END[0] * 60 + PREOPEN_END[1]
    return start <= minutes <= end


def run_date_iso(value: str) -> str:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def run_date_iso(value: str) -> str:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def resolve_runtime_file(path: Path | None, pattern: str) -> Path:
    if path is not None:
        return path if path.is_absolute() else ROOT / path
    return latest_file(pattern)


def resolve_output(path: Path | None, default: str) -> Path:
    if path is not None:
        return path if path.is_absolute() else ROOT / path
    return ROOT / default


def latest_file(pattern: str) -> Path:
    from tools.runtime_files import latest_free_snapshot_file

    if pattern == "data/features/free_snapshots_*.json":
        return latest_free_snapshot_file(ROOT, pattern)
    files = sorted(ROOT.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(pattern)
    return files[0]


def snapshot_trade_date_text(snapshot_path: Path) -> str:
    try:
        rows = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        rows = []
    dates = sorted(
        str(row.get("trade_date") or "").replace("-", "")
        for row in rows
        if isinstance(row, dict) and row.get("trade_date")
    )
    if dates:
        return dates[-1][:8]
    digits = "".join(ch for ch in snapshot_path.stem if ch.isdigit())
    return digits[-8:] if len(digits) >= 8 else datetime.now(SHANGHAI).strftime("%Y%m%d")


def preopen_focus_codes(snapshot_path: Path, max_codes: int = 40) -> list[str]:
    codes: list[str] = []

    def add(code: object) -> None:
        text = str(code or "").strip()
        if text and text not in codes:
            codes.append(text)

    dashboard = load_dashboard_data(ROOT / "web_dashboard" / "data.js")
    for row in dashboard.get("preferred", []):
        if isinstance(row, dict):
            add(row.get("code"))
    for key in ("simulatedTrading", "simulatedTradingA2"):
        sim = dashboard.get(key) if isinstance(dashboard.get(key), dict) else {}
        for row in sim.get("positions", []):
            if isinstance(row, dict):
                add(row.get("code"))
    try:
        snapshots = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        snapshots = []
    for row in snapshots:
        if len(codes) >= max_codes:
            break
        if isinstance(row, dict):
            add(row.get("code"))
    return codes[:max_codes]


def load_dashboard_data(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    prefix = "window.QA_DATA = "
    if prefix not in text:
        return {}
    payload = text.split(prefix, 1)[1].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def context_evidence_metrics(context_evidence: dict) -> dict[str, int]:
    return {
        "codes": len(context_evidence),
        "quotes": sum(1 for item in context_evidence.values() if isinstance(item, dict) and item.get("quote")),
        "themes": sum(1 for item in context_evidence.values() if isinstance(item, dict) and item.get("theme")),
        "news_events": sum(news_event_count(item) for item in context_evidence.values() if isinstance(item, dict)),
    }


def news_event_count(item: dict) -> int:
    news = item.get("news") if isinstance(item, dict) else None
    if not isinstance(news, dict):
        return 0
    events = news.get("events")
    if isinstance(events, list):
        return len(events)
    count = news.get("event_count")
    if isinstance(count, int):
        return count
    return 0


def expand_report_for_snapshot(snapshot_path: Path, snapshot_trade_date: str) -> Path:
    dated = ROOT / "runs" / f"free_pool_expand_{snapshot_trade_date}.json"
    if dated.exists():
        return dated
    reports = sorted((ROOT / "runs").glob("free_pool_expand_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return reports[0] if reports else dated


class DirectoryLock:
    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        try:
            (self.path / "pid").unlink(missing_ok=True)
            self.path.rmdir()
        except OSError:
            pass


def acquire_run_lock(path: Path) -> DirectoryLock | None:
    lock_dir = path.with_name(f"{path.name}.dir")
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_dir.mkdir()
    except FileExistsError:
        if not stale_lock(lock_dir):
            return None
        try:
            (lock_dir / "pid").unlink(missing_ok=True)
            lock_dir.rmdir()
            lock_dir.mkdir()
        except OSError:
            return None
    (lock_dir / "pid").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return DirectoryLock(lock_dir)


def stale_lock(lock_dir: Path) -> bool:
    try:
        payload = json.loads((lock_dir / "pid").read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        return True
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


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


def ensure_json_file(path: Path, payload: dict) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_available(path: Path) -> dict:
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError, KeyError):
        return {}
    return data if isinstance(data, dict) else {}


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
    print(f"institutional_evidence_codes={report.get('institutional_evidence_codes')}")
    print(f"context_evidence_codes={report.get('context_evidence_codes')}")
    print(f"overnight_status={report.get('overnight_status')}")
    print(f"quote_coverage={report.get('quote_coverage')}")
    print(f"theme_coverage={report.get('theme_coverage')}")
    print(f"baseline_selected={','.join(str(code) for code in report.get('baseline_selected', []))}")
    print(f"selected={','.join(str(code) for code in report.get('selected', []))}")


if __name__ == "__main__":
    main()
