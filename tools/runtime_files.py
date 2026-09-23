from __future__ import annotations

import json
import re
from pathlib import Path


def latest_free_snapshot_file(
    root: Path,
    pattern: str = "data/features/free_snapshots_*.json",
    exclude: set[Path] | None = None,
) -> Path:
    excluded = {path.resolve() for path in (exclude or set())}
    files = [
        path
        for path in sorted(root.glob(pattern), key=_snapshot_sort_key, reverse=True)
        if path.resolve() not in excluded
    ]
    if not files:
        raise FileNotFoundError(pattern)

    completed = [path for path in files if _is_complete_snapshot(root, path)]
    if completed:
        return completed[0]

    cached = [path for path in files if _snapshot_row_count(path) >= 1_000]
    if cached:
        return cached[0]

    return files[0]


def _snapshot_sort_key(path: Path) -> tuple[str, float]:
    return (_file_date_digits(path) or "", _safe_mtime(path))


def _is_complete_snapshot(root: Path, snapshot_path: Path) -> bool:
    date_digits = _file_date_digits(snapshot_path)
    if not date_digits:
        return False
    report_path = root / "runs" / f"free_pool_expand_{date_digits}.json"
    if not report_path.exists():
        return False
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("complete") is True


def _snapshot_row_count(path: Path) -> int:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(rows) if isinstance(rows, list) else 0


def _file_date_digits(path: Path) -> str | None:
    digits = re.sub(r"\D", "", path.stem)
    if len(digits) < 8:
        return None
    return digits[-8:]


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
