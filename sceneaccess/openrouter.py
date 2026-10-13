from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.request import Request, urlopen

from .session_store import VideoSession


@dataclass(frozen=True)
class Completion:
    answer: str
    model: str
    prompt_tokens: int
    # None means the provider did not report cache details, which is different from zero.
    cached_tokens: int | None
    cache_write_tokens: int | None

    def usage_record(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


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
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        return Completion(
            answer,
            # The response model is the one that actually answered, e.g. the pick of openrouter/auto.
            model=str(data.get("model") or session.model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            cached_tokens=_optional_int(details.get("cached_tokens")),
            cache_write_tokens=_optional_int(details.get("cache_write_tokens")),
        )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
