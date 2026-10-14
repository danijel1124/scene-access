from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .session_store import VideoSession

# A question may trigger several rounds of tool calls; after this many the model must answer.
MAX_TOOL_ROUNDS = 6


class OpenRouterError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Completion:
    answer: str
    model: str
    prompt_tokens: int
    # None means the provider did not report cache details, which is different from zero.
    cached_tokens: int | None
    cache_write_tokens: int | None
    web_searches: int = 0
    # Requests sent for this answer: one, plus one per round of tool calls.
    requests: int = 1
    tools_unavailable: bool = False
    # The provider that served the answer, e.g. CoreWeave. Each provider keeps its own cache.
    provider: str = ""

    def usage_record(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "web_searches": self.web_searches,
            "requests": self.requests,
        }


@dataclass
class Turn:
    """Everything one question added to the chat, ready to be stored, and the final answer's data."""
    entries: list[dict[str, Any]] = field(default_factory=list)
    completion: Completion | None = None


# Events reported while a turn runs: ("tool_call", text) before a tool runs, ("tool_result", text) after it.
EventHandler = Callable[[str, str], None]


class OpenRouterClient:
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, base_url: str | None = None, opener: Callable[..., Any] = urlopen):
        self.api_key = api_key
        self.endpoint = base_url or self.endpoint
        self.opener = opener

    def _post(self, payload: dict[str, Any], url: str | None = None) -> dict[str, Any]:
        request = Request(
            url or self.endpoint, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=300) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            # The body says why, e.g. that a file is too large or a model does not support tools.
            raise OpenRouterError(f"HTTP {error.code}: {_error_message(error)}", error.code) from None
        if "error" in data:
            raise OpenRouterError(data["error"].get("message", "OpenRouter request failed"), data["error"].get("code"))
        return data

    def transcribe(self, audio: bytes, audio_format: str = "mp3", model: str = "elevenlabs/scribe-v2") -> dict[str, Any]:
        """Speech to text with word timestamps through OpenRouter's transcription endpoint."""
        from .models import api_url

        payload = {"model": model, "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": audio_format},
                   "response_format": "verbose_json", "timestamp_granularities": ["word", "segment"]}
        return self._post(payload, api_url(self.endpoint, "audio/transcriptions"))

    def run_turn(self, session: VideoSession, messages: list[dict[str, Any]], run_tool: Callable[[str, str], Any],
                 on_event: EventHandler | None = None, routing: dict[str, Any] | None = None) -> Turn:
        """Asks the model and runs its tool calls until it answers. run_tool returns a tools.ToolResult.
        routing holds extra request fields such as plugins and provider, see models.routing."""
        from .tools import describe_call, image_message

        notify = on_event or (lambda _kind, _text: None)
        messages = list(messages)
        turn = Turn()
        responses: list[dict[str, Any]] = []
        tools = list(session.tools)
        tools_unavailable = False
        rounds = 0
        while True:
            payload: dict[str, Any] = {"model": session.model, "session_id": session.id, "messages": messages,
                                       **(routing or {})}
            if tools:
                payload["tools"] = tools
                if rounds >= MAX_TOOL_ROUNDS:
                    payload["tool_choice"] = "none"
            try:
                data = self._post(payload)
            except OpenRouterError as error:
                if tools and _rejects_tools(error):
                    # The model cannot use tools; answer without them rather than not at all.
                    tools, tools_unavailable = [], True
                    continue
                raise
            responses.append(data)
            message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            assistant = {key: message[key] for key in ("role", "content", "tool_calls", "reasoning_details") if key in message}
            assistant["role"] = "assistant"
            assistant.setdefault("content", None)
            if calls and rounds >= MAX_TOOL_ROUNDS:
                # The model ignored tool_choice "none". Never run more calls: each round is a paid request.
                # The calls are dropped, so the stored chat has no call without a result.
                calls = []
                assistant.pop("tool_calls", None)
                message = {**message, "content": message.get("content") or
                           f"The model kept calling tools after {MAX_TOOL_ROUNDS} rounds and gave no answer."}
                data = {**data, "choices": [{**data["choices"][0], "message": message}]}
            if not calls:
                completion = _completion(data, session, responses, tools_unavailable)
                assistant["content"] = completion.answer
                assistant["usage"] = completion.usage_record()
                sources = _sources(message)
                if sources:
                    assistant["sources"] = sources
                turn.entries.append(assistant)
                turn.completion = completion
                return turn
            rounds += 1
            turn.entries.append(assistant)
            messages.append(_wire(assistant))
            images = []
            for call in calls:
                function = call.get("function") or {}
                name, arguments = function.get("name", ""), function.get("arguments") or "{}"
                notify("tool_call", describe_call(name, arguments))
                result = run_tool(name, arguments)
                tool_message = {"role": "tool", "tool_call_id": call.get("id", ""), "content": result.text,
                                "caption": result.caption or result.text}
                turn.entries.append(tool_message)
                messages.append(_wire(tool_message))
                notify("tool_result", result.caption or result.text)
                if result.image:
                    images.append(image_message(result))
            # Images follow all tool results: tool messages must directly follow the assistant's calls.
            for image in images:
                turn.entries.append(image)
                messages.append(session.wire_message(image))


def _wire(message: dict[str, Any]) -> dict[str, Any]:
    from .session_store import WIRE_KEYS
    return {key: message[key] for key in WIRE_KEYS if key in message}


def _rejects_tools(error: OpenRouterError) -> bool:
    text = str(error).lower()
    return error.status in (400, 404) and ("tool" in text or "function" in text)


def _error_message(error: HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:
        return error.reason or "request failed"
    try:
        data = json.loads(body)
        message = data.get("error", {}).get("message") or body
        raw = (data.get("error", {}).get("metadata") or {}).get("raw")
        return f"{message} ({raw})" if raw and raw not in message else message
    except (ValueError, AttributeError):
        return body.strip()[:500] or (error.reason or "request failed")


def _sources(message: dict[str, Any]) -> list[dict[str, str]]:
    sources, seen = [], set()
    for annotation in message.get("annotations") or []:
        citation = annotation.get("url_citation") if annotation.get("type") == "url_citation" else None
        if citation and citation.get("url") and citation["url"] not in seen:
            seen.add(citation["url"])
            sources.append({"title": str(citation.get("title") or citation["url"]), "url": str(citation["url"])})
    return sources


def _completion(data: dict[str, Any], session: VideoSession, responses: list[dict[str, Any]],
                tools_unavailable: bool = False) -> Completion:
    usages = [response.get("usage") or {} for response in responses]
    details = [usage.get("prompt_tokens_details") or {} for usage in usages]
    return Completion(
        data["choices"][0]["message"].get("content") or "",
        # The response model is the one that actually answered, e.g. the pick of openrouter/auto.
        model=str(data.get("model") or session.model),
        prompt_tokens=sum(int(usage.get("prompt_tokens") or 0) for usage in usages),
        cached_tokens=_optional_sum(detail.get("cached_tokens") for detail in details),
        cache_write_tokens=_optional_sum(detail.get("cache_write_tokens") for detail in details),
        web_searches=sum(int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0) for usage in usages),
        requests=len(responses),
        tools_unavailable=tools_unavailable,
        provider=str(data.get("provider") or ""),
    )


def _optional_sum(values: Any) -> int | None:
    numbers = [int(value) for value in values if value is not None]
    return sum(numbers) if numbers else None
