from __future__ import annotations

import subprocess


def add_apple_reminder(title: str, body: str | None = None) -> tuple[bool, str]:
    note = body or ""
    script = (
        'tell application "Reminders"\n'
        'set targetList to default list\n'
        f'make new reminder at end of reminders of targetList with properties {{name:{_osa(title)}, body:{_osa(note)}}}\n'
        "end tell\n"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as error:
        return False, str(error)
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, result.stdout.strip()


def _osa(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

