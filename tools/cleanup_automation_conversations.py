from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


CODEX_HOME = Path("/Users/a0000/.codex")
TARGET_NAMES = (
    "A股免费股票池自动扩展",
    "A股开盘前信息检查",
    "A股免费数据每日更新",
    "A股盘中轻量更新",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove Codex conversations created by A-share automations.")
    parser.add_argument("--execute", action="store_true", help="Actually delete/update files. Without this, only report matches.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    matches = find_thread_matches()
    ids = [row["id"] for row in matches]
    paths = sorted(find_rollout_paths(ids))
    shell_paths = sorted(find_shell_snapshot_paths(ids))
    index_removed = prune_session_index(ids, args.execute)
    db_counts = prune_databases(ids, args.execute)
    deleted_files = delete_paths([*paths, *shell_paths], args.execute)

    report = {
        "executed": args.execute,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_names": TARGET_NAMES,
        "matched_threads": matches,
        "session_index_removed": index_removed,
        "rollout_files": [str(path) for path in paths],
        "shell_snapshot_files": [str(path) for path in shell_paths],
        "deleted_files": [str(path) for path in deleted_files],
        "database_changes": db_counts,
    }
    report_path = Path("runs") / f"automation_conversation_cleanup_{datetime.now():%Y%m%d_%H%M%S}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"matched_threads={len(matches)}")
    print(f"session_index_removed={index_removed}")
    print(f"rollout_files={len(paths)}")
    print(f"shell_snapshot_files={len(shell_paths)}")
    print(f"deleted_files={len(deleted_files)}")
    print(f"database_changes={db_counts}")
    print(f"report={report_path}")


def find_thread_matches() -> list[dict[str, str | int]]:
    db_path = CODEX_HOME / "state_5.sqlite"
    with sqlite3.connect(db_path) as con:
        rows = con.execute("select id, title, archived, rollout_path from threads").fetchall()
    matches = []
    for thread_id, title, archived, rollout_path in rows:
        if not str(title).startswith("Automation:"):
            continue
        if not any(name in str(title) for name in TARGET_NAMES):
            continue
        first_line = str(title).splitlines()[0]
        matches.append(
            {
                "id": str(thread_id),
                "title": first_line,
                "archived": int(archived),
                "rollout_path": str(rollout_path),
            }
        )
    return matches


def find_rollout_paths(ids: list[str]) -> set[Path]:
    roots = [CODEX_HOME / "sessions", CODEX_HOME / "archived_sessions"]
    paths: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            if any(thread_id in path.name for thread_id in ids):
                paths.add(path)
    return paths


def find_shell_snapshot_paths(ids: list[str]) -> set[Path]:
    root = CODEX_HOME / "shell_snapshots"
    if not root.exists():
        return set()
    return {path for path in root.iterdir() if any(thread_id in path.name for thread_id in ids)}


def prune_session_index(ids: list[str], execute: bool) -> int:
    index_path = CODEX_HOME / "session_index.jsonl"
    if not index_path.exists():
        return 0
    lines = index_path.read_text(encoding="utf-8").splitlines()
    kept = []
    removed = 0
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if str(item.get("id")) in ids:
            removed += 1
        else:
            kept.append(line)
    if execute:
        index_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


def prune_databases(ids: list[str], execute: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not ids:
        return counts
    placeholders = ",".join("?" for _ in ids)
    if execute:
        with sqlite3.connect(CODEX_HOME / "state_5.sqlite") as con:
            con.execute("pragma foreign_keys=on")
            counts["threads"] = con.execute(f"select count(*) from threads where id in ({placeholders})", ids).fetchone()[0]
            counts["thread_dynamic_tools"] = con.execute(
                f"select count(*) from thread_dynamic_tools where thread_id in ({placeholders})",
                ids,
            ).fetchone()[0]
            counts["stage1_outputs"] = con.execute(
                f"select count(*) from stage1_outputs where thread_id in ({placeholders})",
                ids,
            ).fetchone()[0]
            con.execute(f"delete from threads where id in ({placeholders})", ids)
            con.execute(f"delete from thread_spawn_edges where parent_thread_id in ({placeholders}) or child_thread_id in ({placeholders})", ids + ids)
        with sqlite3.connect(CODEX_HOME / "logs_2.sqlite") as con:
            counts["logs"] = con.execute(f"select count(*) from logs where thread_id in ({placeholders})", ids).fetchone()[0]
            con.execute(f"delete from logs where thread_id in ({placeholders})", ids)
        with sqlite3.connect(CODEX_HOME / "goals_1.sqlite") as con:
            counts["thread_goals"] = con.execute(
                f"select count(*) from thread_goals where thread_id in ({placeholders})",
                ids,
            ).fetchone()[0]
            con.execute(f"delete from thread_goals where thread_id in ({placeholders})", ids)
    else:
        with sqlite3.connect(CODEX_HOME / "state_5.sqlite") as con:
            counts["threads"] = con.execute(f"select count(*) from threads where id in ({placeholders})", ids).fetchone()[0]
        with sqlite3.connect(CODEX_HOME / "logs_2.sqlite") as con:
            counts["logs"] = con.execute(f"select count(*) from logs where thread_id in ({placeholders})", ids).fetchone()[0]
        with sqlite3.connect(CODEX_HOME / "goals_1.sqlite") as con:
            counts["thread_goals"] = con.execute(
                f"select count(*) from thread_goals where thread_id in ({placeholders})",
                ids,
            ).fetchone()[0]
    return counts


def delete_paths(paths: list[Path], execute: bool) -> list[Path]:
    deleted = []
    for path in paths:
        if not path.exists():
            continue
        if execute:
            path.unlink()
        deleted.append(path)
    return deleted


if __name__ == "__main__":
    main()
