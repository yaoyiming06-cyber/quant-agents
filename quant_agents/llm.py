from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .env import load_project_env


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "rules"
    api_key: str | None = None
    model: str = "deepseek-v4-pro"
    base_url: str = "https://api.deepseek.com"
    timeout: int = 30


class DeepSeekClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        load_project_env()
        self.config = config or LLMConfig(
            provider=os.getenv("LLM_PROVIDER", "rules"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        )

    @property
    def available(self) -> bool:
        return self.config.provider == "deepseek" and bool(self.config.api_key)

    def chat_json(self, system: str, user: str, max_tokens: int = 700) -> dict[str, Any]:
        if not self.available:
            raise LLMError("deepseek_not_configured")
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "quant-agents/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise LLMError(f"deepseek_request_failed:{error}") from error
        try:
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(_extract_json_object(str(content)))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise LLMError("deepseek_invalid_json_response") from error
        if not isinstance(parsed, dict):
            raise LLMError("deepseek_json_not_object")
        return parsed


def configured_llm() -> DeepSeekClient:
    return DeepSeekClient()


def _extract_json_object(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text
