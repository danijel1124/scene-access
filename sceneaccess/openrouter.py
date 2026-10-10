from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.request import Request, urlopen

from .session_store import VideoSession


@dataclass(frozen=True)
class Completion:
    answer: str
    prompt_tokens: int
    cached_tokens: int


class OpenRouterClient:
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, base_url: str | None = None, opener: Callable[..., Any] = urlopen):
        self.api_key = api_key
        self.endpoint = base_url or self.endpoint
        self.opener = opener

    def complete(self, session: VideoSession, messages: list[dict[str, Any]]) -> Completion:
        payload = json.dumps({
            "model": session.model,
            "session_id": session.id,
            "messages": messages,
        }).encode("utf-8")
        request = Request(
            self.endpoint, data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with self.opener(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "OpenRouter request failed"))
        answer = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return Completion(answer, int(usage.get("prompt_tokens", 0)), int(usage.get("cached_tokens", 0)))
