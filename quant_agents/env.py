from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_project_env(path: Path | str | None = None) -> None:
    env_path = Path(path) if path else ROOT / ".env"
    try:
        if not env_path.exists():
            return
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        # Cloud-backed project files can be temporarily locked/dataless.
        # The .env file is optional, so automation should continue without it.
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def masked_env_status() -> dict[str, str]:
    load_project_env()
    keys = ("DEEPSEEK_API_KEY", "LLM_PROVIDER", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL")
    return {key: "set" if os.getenv(key) else "missing" for key in keys}
