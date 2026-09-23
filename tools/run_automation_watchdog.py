"""Lightweight safety net for missed quant automation launches."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")
OK_STATUSES = {"ok"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill missed daily automation tasks.")
    parser.add_argument("--date", type=str, default=None, help="Run date as YYYYMMDD; defaults to Asia/Shanghai today.")
    parser.add_argument("--dry-run", action="store_true", help="Record actions without running them.")
    return parser.parse_args()


def required_actions(root: Path = ROOT, now: datetime | None = None) -> list[dict[str, object]]:
    now = now or datetime.now(SHANGHAI)
    if now.weekday() >= 5:
        return []

    run_date = now.strftime("%Y%m%d")
    minute_of_day = now.hour * 60 + now.minute
    actions: list[dict[str, object]] = []

    if 8 * 60 + 45 <= minute_of_day <= 9 * 60 + 25:
        if not report_ok(root / "runs" / f"preopen_check_report_{run_date}.json"):
            actions.append(action("preopen", run_date, "tools/run_preopen_check.py"))

    if in_intraday_watch_window(minute_of_day):
        if not report_ok(root / "runs" / f"intraday_update_report_{run_date}.json"):
            actions.append(action("intraday", run_date, "tools/run_intraday_update.py"))

    if minute_of_day >= 15 * 60 + 25:
        if not report_ok(root / "runs" / f"post_close_update_report_{run_date}.json"):
            actions.append(action("post_close", run_date, "tools/run_post_close_update.py"))

    return actions


def in_intraday_watch_window(minute_of_day: int) -> bool:
    return (9 * 60 + 45 <= minute_of_day <= 11 * 60 + 35) or (
        13 * 60 + 15 <= minute_of_day <= 15 * 60 + 20
    )


def action(key: str, run_date: str, script: str) -> dict[str, object]:
    return {
        "key": key,
        "command": [sys.executable, script, "--date", run_date, "--force"],
    }


def report_ok(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") in OK_STATUSES


def run_action(root: Path, action_item: dict[str, object], dry_run: bool = False) -> dict[str, object]:
    command = [str(part) for part in action_item["command"]]
    result = {"key": action_item["key"], "command": command, "dry_run": dry_run}
    if dry_run:
        result["returncode"] = 0
        return result

    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    result.update(
        {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    )
    return result


def write_report(root: Path, payload: dict[str, object], now: datetime) -> None:
    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_date = now.strftime("%Y%m%d")
    stamp = now.strftime("%H%M%S")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    (runs / f"automation_watchdog_report_{run_date}_{stamp}.json").write_text(text, encoding="utf-8")
    (runs / "automation_watchdog_latest.json").write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    now = datetime.now(SHANGHAI)
    if args.date:
        now = datetime.strptime(args.date, "%Y%m%d").replace(
            hour=now.hour,
            minute=now.minute,
            second=now.second,
            tzinfo=SHANGHAI,
        )

    actions = required_actions(ROOT, now)
    results = [run_action(ROOT, action_item, dry_run=args.dry_run) for action_item in actions]
    status = "ok" if all(item.get("returncode") == 0 for item in results) else "error"
    payload = {
        "status": status,
        "run_date": now.strftime("%Y%m%d"),
        "checked_at": now.isoformat(timespec="seconds"),
        "actions": actions,
        "results": results,
    }
    write_report(ROOT, payload, now)

    print(f"status={status}")
    print(f"actions={','.join(str(item['key']) for item in actions)}")
    if status != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
