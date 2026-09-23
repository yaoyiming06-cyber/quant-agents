"""Check archived dashboard intraday snapshots for missing 15-minute slots."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_SLOTS = (
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check intraday dashboard snapshot completeness.")
    parser.add_argument("--date", default=None, help="Trade date as YYYYMMDD; defaults to Asia/Shanghai today.")
    parser.add_argument("--codes", nargs="*", default=None, help="Optional stock codes to require in each snapshot.")
    parser.add_argument("--report", type=Path, default=None, help="Optional JSON report path.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Project root override for tests.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_text = args.date or datetime.now(SHANGHAI).strftime("%Y%m%d")
    root = args.root if args.root.is_absolute() else ROOT / args.root
    report = build_completeness_report(date_text=date_text, root=root, codes=args.codes)
    report_path = args.report or root / "runs" / f"intraday_snapshot_completeness_{report['dateCompact']}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"complete={report['complete']}")
    print(f"missingSlots={','.join(report['missingSlots']) or '-'}")
    print(f"report={display_path(report_path, root)}")
    if not report["complete"]:
        raise SystemExit(1)


def build_completeness_report(
    date_text: str,
    root: Path = ROOT,
    expected_slots: list[str] | tuple[str, ...] = DEFAULT_SLOTS,
    codes: list[str] | None = None,
) -> dict:
    date_compact = compact_date(date_text)
    required_codes = [normalise_code(code) for code in (codes or []) if normalise_code(code)]
    snapshots = snapshots_by_slot(root, date_compact)
    missing_slots: list[str] = []
    missing_codes_by_slot: dict[str, list[str]] = {}
    present_slots: list[str] = []

    for slot in expected_slots:
        snapshot = snapshots.get(slot)
        if snapshot is None:
            missing_slots.append(slot)
            missing_codes_by_slot[slot] = required_codes.copy()
            continue
        present_slots.append(slot)
        present_codes = stock_codes_in_snapshot(snapshot)
        missing_codes = [code for code in required_codes if code not in present_codes]
        missing_codes_by_slot[slot] = missing_codes

    missing_code_slots = [slot for slot, missing in missing_codes_by_slot.items() if missing]
    complete = not missing_slots and not missing_code_slots
    return {
        "date": f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:8]}",
        "dateCompact": date_compact,
        "expectedSlots": list(expected_slots),
        "presentSlots": present_slots,
        "missingSlots": missing_slots,
        "requiredCodes": required_codes,
        "missingCodesBySlot": missing_codes_by_slot,
        "complete": complete,
        "repairMode": "best_effort_from_existing_archives_only",
        "repairNote": "历史盘中报价如果未被任何快照保存，盘后无法无损还原；脚本只检测缺口并从已有归档判断可用性，不伪造价格。",
    }


def snapshots_by_slot(root: Path, date_compact: str) -> dict[str, dict]:
    archive_dir = root / "web_dashboard" / "archive"
    snapshots: dict[str, dict] = {}
    if not archive_dir.exists():
        return snapshots
    for path in sorted(archive_dir.glob(f"data_{date_compact}*.js")):
        stamp = path.stem.replace("data_", "")
        if len(stamp) < 12:
            continue
        slot = f"{stamp[8:10]}:{stamp[10:12]}"
        data = load_dashboard_archive(path)
        if isinstance(data, dict):
            snapshots[slot] = data
    return snapshots


def load_dashboard_archive(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    prefix = "window.QA_DATA = "
    if text.startswith(prefix):
        text = text.removeprefix(prefix).rstrip(";\n")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def stock_codes_in_snapshot(snapshot: dict) -> set[str]:
    codes: set[str] = set()
    for section in ("preferred", "history"):
        for row in snapshot.get(section, []):
            if isinstance(row, dict) and (code := normalise_code(row.get("code"))):
                codes.add(code)
    ledger = snapshot.get("tradeLedger") if isinstance(snapshot.get("tradeLedger"), dict) else {}
    for row in ledger.get("records", []):
        if isinstance(row, dict) and (code := normalise_code(row.get("code"))):
            codes.add(code)
    return codes


def compact_date(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 8:
        raise ValueError(f"invalid date: {value}")
    return digits[:8]


def normalise_code(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 6:
        return None
    return digits[-6:]


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
