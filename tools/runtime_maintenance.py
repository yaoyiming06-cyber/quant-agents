from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLOUD_ERRNOS = {errno.EAGAIN, errno.EDEADLK}
WEB_SUFFIXES = {".html", ".js", ".css"}
RECENT_EVIDENCE_LIMIT = 32
RECENT_RUNS_LIMIT = 64


def is_cloud_io_error(error: OSError) -> bool:
    return getattr(error, "errno", None) in CLOUD_ERRNOS


def probe_file(path: Path) -> None:
    with path.open("rb") as handle:
        handle.read(1)


def read_file_bytes(path: Path) -> bytes:
    return path.read_bytes()


def write_file_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


def restore_file_times(path: Path, stat_result: os.stat_result) -> None:
    try:
        os.utime(path, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns))
    except OSError:
        pass


def trigger_cloud_download(path: Path) -> dict[str, Any]:
    brctl = Path("/usr/bin/brctl")
    if not brctl.exists():
        return {"attempted": False, "reason": "brctl_missing"}
    completed = subprocess.run(
        [str(brctl), "download", str(path)],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    return {
        "attempted": True,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-500:],
    }


def materialize_file(path: Path, attempts: int = 3, rewrite: bool = True) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    if path.is_dir():
        return {"path": str(path), "status": "skipped_dir"}
    original_stat = path.stat()
    if original_stat.st_size == 0:
        recovered = restore_zero_length_dashboard_asset(path)
        if recovered is not None:
            return recovered
        return {"path": str(path), "status": "error", "error": "zero_length_file"}

    try:
        probe_file(path)
        return {"path": str(path), "status": "ok", "bytes": original_stat.st_size}
    except OSError as error:
        if not is_cloud_io_error(error):
            return {"path": str(path), "status": "error", "error": repr(error)}

    downloads: list[dict[str, Any]] = []
    last_error: OSError | None = None
    for attempt in range(max(1, attempts)):
        downloads.append(trigger_cloud_download(path))
        time.sleep(0.15 * (attempt + 1))
        try:
            payload = read_file_bytes(path)
            if rewrite:
                write_file_bytes(path, payload)
                restore_file_times(path, original_stat)
            return {
                "path": str(path),
                "status": "materialized",
                "bytes": len(payload),
                "downloads": downloads,
            }
        except OSError as error:
            last_error = error
            if not is_cloud_io_error(error):
                break

    return {
        "path": str(path),
        "status": "error",
        "error": repr(last_error),
        "downloads": downloads,
    }


def restore_zero_length_dashboard_asset(path: Path) -> dict[str, Any] | None:
    if path.parent.name != "web_dashboard" or path.name != "data.js":
        return None
    archive_dir = path.parent / "archive"
    if not archive_dir.exists():
        return None
    archives = [item for item in archive_dir.glob("data_*.js") if item.is_file() and item.stat().st_size > 0]
    if not archives:
        return None
    latest = max(archives, key=lambda item: item.stat().st_mtime)
    payload = latest.read_bytes()
    path.write_bytes(payload)
    return {
        "path": str(path),
        "status": "materialized",
        "bytes": len(payload),
        "restoredFrom": str(latest),
    }


def recent_json_files(directory: Path, limit: int) -> list[Path]:
    files: list[tuple[float, str, Path]] = []
    for path in directory.glob("*.json"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        files.append((mtime, str(path), path))
    files.sort(reverse=True)
    return [path for _mtime, _name, path in files[:limit]]


def runtime_targets(root: Path = ROOT) -> list[tuple[Path, bool]]:
    targets: list[tuple[Path, bool]] = [
        (root / "data" / "features" / "global_sector_flow_state.json", True),
        (root / "runs" / "intraday_update_state.json", True),
    ]
    features_dir = root / "data" / "features"
    if features_dir.exists():
        targets.extend((path, True) for path in sorted(features_dir.glob("*.json")) if path.is_file())
    evidence_dir = root / "data" / "evidence"
    if evidence_dir.exists():
        targets.extend((path, True) for path in recent_json_files(evidence_dir, RECENT_EVIDENCE_LIMIT))
    runs_dir = root / "runs"
    if runs_dir.exists():
        targets.extend((path, True) for path in recent_json_files(runs_dir, RECENT_RUNS_LIMIT))
    web_dir = root / "web_dashboard"
    if web_dir.exists():
        targets.extend((path, True) for path in sorted(web_dir.iterdir()) if path.is_file() and path.suffix in WEB_SUFFIXES)
    seen: set[Path] = set()
    unique_targets: list[tuple[Path, bool]] = []
    for path, rewrite in targets:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique_targets.append((path, rewrite))
    return unique_targets


def run_maintenance(root: Path = ROOT, quiet: bool = False) -> dict[str, Any]:
    results = []
    for path, rewrite in runtime_targets(root):
        results.append(materialize_file(path, rewrite=rewrite))
    summary = {
        "root": str(root),
        "checkedAt": datetime.now().isoformat(timespec="seconds"),
        "checked": len(results),
        "materialized": sum(1 for item in results if item.get("status") == "materialized"),
        "errors": [item for item in results if item.get("status") == "error"],
        "missing": [item for item in results if item.get("status") == "missing"],
        "results": results,
    }
    if not quiet:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize runtime files that may have become cloud placeholders.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--quick", action="store_true", help="Kept for launchd readability; current target set is already quick.")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_maintenance(args.root, quiet=args.quiet)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
