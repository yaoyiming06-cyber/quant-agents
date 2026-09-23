from __future__ import annotations

import json
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path


CODEX_HOME = Path("/Users/a0000/.codex")


def main() -> None:
    now = int(time.time())
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    state_path = CODEX_HOME / "state_5.sqlite"
    index_path = CODEX_HOME / "session_index.jsonl"
    archive_dir = CODEX_HOME / "archived_sessions"
    backup_dir = Path("/tmp") / f"codex_automation_archive_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(state_path, backup_dir / state_path.name)
    if index_path.exists():
        shutil.copy2(index_path, backup_dir / index_path.name)
    archive_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(state_path) as con:
        rows = con.execute(
            """
            select id, rollout_path
            from threads
            where archived = 0 and title like 'Automation:%'
            """
        ).fetchall()

        archived: list[dict[str, str]] = []
        for thread_id, rollout_path in rows:
            source = Path(rollout_path)
            destination = archive_dir / source.name
            if source.exists() and source != destination:
                if destination.exists():
                    destination.unlink()
                shutil.move(str(source), str(destination))
            elif not destination.exists():
                destination = source

            con.execute(
                """
                update threads
                set archived = 1, archived_at = ?, rollout_path = ?
                where id = ?
                """,
                (now, str(destination), thread_id),
            )
            archived.append(
                {
                    "id": str(thread_id),
                    "rollout_path": str(destination),
                }
            )

    removed_from_index = 0
    archived_ids = {row["id"] for row in archived}
    if index_path.exists() and archived_ids:
        kept: list[str] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if str(item.get("id")) in archived_ids:
                removed_from_index += 1
            else:
                kept.append(line)
        index_path.write_text(
            "\n".join(kept) + ("\n" if kept else ""),
            encoding="utf-8",
        )

    report = {
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "archived_count": len(archived),
        "removed_from_session_index": removed_from_index,
        "backup_dir": str(backup_dir),
        "threads": archived,
    }
    report_path = Path("runs") / f"automation_conversation_archive_{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"archived_count={len(archived)}")
    print(f"removed_from_session_index={removed_from_index}")
    print(f"backup_dir={backup_dir}")
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
