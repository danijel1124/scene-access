from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler

from .models import api_url
from ..providers import OPENROUTER_URL
from ..request_control import RequestControl
from ..storage.session_store import WIRE_KEYS, VideoSession
from .tool_calls import describe_call, image_message
from ..worker_process import run_isolated_task

# A question may trigger several rounds of tool calls; after this many the model must answer.
MAX_TOOL_ROUNDS = 6
# How long a streamed answer may stay silent, in seconds. Before its first word comes the whole prompt is read,
# which on the user's own llama.cpp server can take minutes for a video; see LLAMA_CPP_READ_TIMEOUT.
READ_TIMEOUT = 300
LLAMA_CPP_READ_TIMEOUT = 900


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
    # Why the answer ended: "stop" when done, "length" when cut off at the token or context limit.
    finish_reason: str = ""

    @property
    def cut_off(self) -> bool:
        """True when the answer did not end on its own; whatever it holds is not a whole answer."""
        return self.finish_reason == "length"

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


class AnswerCutOff(RuntimeError):
    """An answer ended at the token or context limit, or empty: it is not stored as an answer."""


def check_whole(completion: Completion, what: str = "The answer") -> None:
    """Raises AnswerCutOff for an answer that is cut off or empty, so it is never stored as a whole one."""
    if completion.cut_off or not completion.answer.strip():
        raise AnswerCutOff(f"{what} was {'cut off at the token limit' if completion.cut_off else 'empty'}.")


@dataclass
class Turn:
    """Everything one question added to the chat, ready to be stored, and the final answer's data."""
    entries: list[dict[str, Any]] = field(default_factory=list)
    completion: Completion | None = None


# Events reported while a turn runs: ("tool_call", text) before a tool runs, ("tool_result", text) after it.
EventHandler = Callable[[str, str], None]
# Pieces of a streamed answer as they arrive: ("thinking", text) for the model's reasoning, ("text", text) for
# what it writes.
DeltaHandler = Callable[[str, str], None]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward the bearer token to a redirected host.


def _request_json(url: str, body: bytes, headers: dict[str, str]) -> tuple[int | None, Any]:
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with build_opener(_NoRedirect).open(request, timeout=300) as response:
            return None, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, _error_message(error)


def _request_stream(url: str, body: bytes, headers: dict[str, str], read_timeout: float = READ_TIMEOUT,
                    _progress_pipe: Any = None) -> tuple[int | None, Any]:
    """Runs in a worker process: streams the request and sends each piece back through the progress pipe."""
    request = Request(url, data=body, headers=headers, method="POST")
    report = (lambda kind, text: _progress_pipe.send(("progress", (kind, text)))) if _progress_pipe else (lambda *_: None)
    try:
        with build_opener(_NoRedirect).open(request, timeout=read_timeout) as response:
            return None, _read_stream(response, report)
    except HTTPError as error:
        return error.code, _error_message(error)


def _read_stream(response: Any, on_delta: DeltaHandler) -> dict[str, Any]:
    """Puts a streamed answer together into the response a request that is not streamed gets, reporting its
    reasoning and text to on_delta as they arrive. An error sent in the stream is returned as {"error": ...}."""
    message: dict[str, Any] = {"role": "assistant", "content": "", "reasoning": ""}
    calls: dict[int, dict[str, Any]] = {}
    details: dict[tuple[Any, Any], dict[str, Any]] = {}
    annotations: list[dict[str, Any]] = []
    data: dict[str, Any] = {}
    finish_reason = None
    unstreamed, streamed = [], False
    for raw in response:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            unstreamed.append(raw)
            continue
        streamed = True
        body = line[len("data:"):].strip()
        if body == "[DONE]":
            break
        chunk = json.loads(body)
        if "error" in chunk:
            return {"error": chunk["error"]}
        data.update({key: chunk[key] for key in ("model", "provider", "usage") if chunk.get(key)})
        for choice in chunk.get("choices") or []:
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            # llama.cpp calls the reasoning reasoning_content, OpenRouter reasoning.
            thinking = delta.get("reasoning_content") or delta.get("reasoning")
            if thinking:
                message["reasoning"] += thinking
                on_delta("thinking", thinking)
            if delta.get("content"):
                message["content"] += delta["content"]
                on_delta("text", delta["content"])
            for call in delta.get("tool_calls") or []:
                slot = calls.setdefault(call.get("index", len(calls)),
                                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                slot["id"] = call.get("id") or slot["id"]
                function = call.get("function") or {}
                slot["function"]["name"] += function.get("name") or ""
                slot["function"]["arguments"] += function.get("arguments") or ""
            for detail in delta.get("reasoning_details") or []:
                _merge_detail(details, detail)
            annotations += delta.get("annotations") or []
    if not streamed:
        # A server or proxy that does not stream answers in one piece; that is the response itself.
        return json.loads(b"".join(unstreamed).decode("utf-8"))
    # As in a response that is not streamed: no text is None, not "", and no reasoning is none at all.
    message["content"] = message["content"] or None
    if not message["reasoning"]:
        del message["reasoning"]
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    if details:
        # Some models need their reasoning back with the tool results, as in a response that is not streamed.
        message["reasoning_details"] = list(details.values())
    if annotations:
        message["annotations"] = annotations
    return {**data, "choices": [{"message": message, "finish_reason": finish_reason}]}


class OpenRouterClient:
    """Chat completions with OpenRouter, or with a llama.cpp server at another endpoint."""
    endpoint = OPENROUTER_URL

    def __init__(self, api_key: str, base_url: str | None = None, opener: Callable[..., Any] = urlopen,
                 control: RequestControl | None = None, ignored_providers: tuple[str, ...] = (),
                 read_timeout: float = READ_TIMEOUT):
        """read_timeout is how long a streamed answer may stay silent, see READ_TIMEOUT."""
        self.api_key = api_key
        self.read_timeout = read_timeout
        self.endpoint = base_url or self.endpoint
        self.opener = opener
        self.control = control
        self.ignored_providers = ignored_providers

    def transcription_allowed(self, model: str = 'elevenlabs/scribe-v2') -> bool:
        vendor = model.split('/', 1)[0].casefold()
        return vendor not in {name.casefold() for name in self.ignored_providers}

    def _request(self, payload: dict[str, Any], url: str | None = None) -> Request:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            # A llama.cpp server without --api-key needs none.
            headers["Authorization"] = f"Bearer {self.api_key}"
        return Request(url or self.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")

    def _post(self, payload: dict[str, Any], url: str | None = None) -> dict[str, Any]:
        if self.control:
            self.control.check_cancelled()
        request = self._request(payload, url)
        try:
            if self.control and self.opener is urlopen:
                data = self._controlled_post(request)
            else:
                with self.opener(request, timeout=300) as response:
                    data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            # The body says why, e.g. that a file is too large or a model does not support tools.
            raise OpenRouterError(f"HTTP {error.code}: {_error_message(error)}", error.code) from None
        if self.control:
            self.control.check_cancelled()
        if "error" in data:
            raise OpenRouterError(data["error"].get("message", "OpenRouter request failed"), data["error"].get("code"))
        return data

    def _post_stream(self, payload: dict[str, Any], on_delta: DeltaHandler) -> dict[str, Any]:
        """Like _post, but streamed: on_delta gets the reasoning and the text as they arrive, and the pieces are
        put together into the same response _post returns. The time limit applies to each read, so a long
        answer that keeps coming is not cut off. A request that can be cancelled is streamed in a worker
        process, which reports the pieces back."""
        if self.control:
            self.control.check_cancelled()
        request = self._request({**payload, "stream": True, "stream_options": {"include_usage": True}})
        if self.control and self.opener is urlopen:
            # An answer may take long while it keeps coming; a stalled one fails on its own after read_timeout.
            status, data = run_isolated_task(_request_stream, (request.full_url, request.data, dict(request.header_items()),
                                                               self.read_timeout),
                                             control=self.control, timeout=3600,
                                             progress_callback=lambda piece: on_delta(*piece))
            if status is not None:
                raise OpenRouterError(f"HTTP {status}: {data}", status)
        else:
            try:
                with self.opener(request, timeout=self.read_timeout) as response:
                    data = _read_stream(response, on_delta)
            except HTTPError as error:
                raise OpenRouterError(f"HTTP {error.code}: {_error_message(error)}", error.code) from None
        if self.control:
            self.control.check_cancelled()
        if "error" in data:
            raise OpenRouterError(data["error"].get("message", "Request failed"), data["error"].get("code"))
        return data

    def _controlled_post(self, request: Request) -> dict[str, Any]:
        """Keep urllib's TLS/proxy support while making the whole request interruptible."""
        assert self.control is not None
        status, data = run_isolated_task(_request_json,
                                      (request.full_url, request.data, dict(request.header_items())),
                                      control=self.control, timeout=330)
        if status is not None:
            raise OpenRouterError(f"HTTP {status}: {data}", status)
        return data

    def transcribe(self, audio: bytes, audio_format: str = "mp3", model: str = "elevenlabs/scribe-v2") -> dict[str, Any]:
        """Speech to text with word timestamps through OpenRouter's transcription endpoint."""
        if not self.transcription_allowed(model):
            raise ValueError('Speech recognition skipped because the transcription provider is excluded.')
        payload = {"model": model, "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": audio_format},
                   "response_format": "verbose_json", "timestamp_granularities": ["word", "segment"]}
        return self._post(payload, api_url(self.endpoint, "audio/transcriptions"))

    def web_search(self, query: str, model: str, routing: dict[str, Any] | None = None) -> tuple[str, list[dict[str, str]]]:
        """Searches the web with OpenRouter's search for a model elsewhere: only the query is sent.
        routing holds the exclusions, see models.routing. Returns the findings and their sources."""
        payload = {"model": model, "messages": [{"role": "user", "content": (
            "Search the web for the following and report the relevant findings briefly, each with its source URL. "
            f"Do not answer from memory.\n\n{query}")}],
            "tools": [{"type": "openrouter:web_search", "parameters": {"max_results": 5}}], **(routing or {})}
        message = self._post(payload)["choices"][0]["message"]
        return message.get("content") or "", _sources(message)

    def run_turn(self, session: VideoSession, messages: list[dict[str, Any]], run_tool: Callable[[str, str], Any],
                 on_event: EventHandler | None = None, routing: dict[str, Any] | None = None,
                 max_tool_rounds: int = MAX_TOOL_ROUNDS, on_delta: DeltaHandler | None = None) -> Turn:
        """Asks the model and runs its tool calls until it answers. run_tool returns a tool_calls.ToolResult.
        routing holds extra request fields such as plugins and provider, see models.routing. With on_delta, each
        request is streamed and on_delta gets its reasoning and text as they arrive."""
        notify = on_event or (lambda _kind, _text: None)
        if type(max_tool_rounds) is not int or max_tool_rounds < 0:
            raise ValueError("The maximum number of tool rounds must be a nonnegative integer.")
        messages = list(messages)
        turn = Turn()
        responses: list[dict[str, Any]] = []
        tools = list(session.tools) if max_tool_rounds else []
        tools_unavailable = False
        rounds = 0
        while True:
            if self.control:
                self.control.checkpoint(lambda: notify("paused", "Request paused after the completed round."))
            payload: dict[str, Any] = {"model": session.model, "session_id": session.id, "messages": messages,
                                       **(routing or {})}
            if tools:
                payload["tools"] = tools
                if rounds >= max_tool_rounds:
                    payload["tool_choice"] = "none"
            try:
                data = self._post_stream(payload, on_delta) if on_delta else self._post(payload)
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
            # Kept for the Chat history and, on a llama.cpp server, sent back within this answer (below); OpenRouter
            # calls it reasoning, llama.cpp reasoning_content.
            thinking = message.get("reasoning") or message.get("reasoning_content")
            if thinking:
                assistant["thinking"] = thinking
            if calls and (not tools or rounds >= max_tool_rounds):
                # The model ignored tool_choice "none". Never run more calls: each round is a paid request.
                # The calls are dropped, so the stored chat has no call without a result.
                calls = []
                assistant.pop("tool_calls", None)
                message = {**message, "content": message.get("content") or
                           f"The model kept calling tools after {max_tool_rounds} rounds and gave no answer."}
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
            wire = _wire(assistant)
            if thinking and self.endpoint != OPENROUTER_URL:
                # The reasoning of a round with tool calls goes back to a llama.cpp server as reasoning_content,
                # only within this answer: Qwen3.5's chat template puts it before the calls, and Gemma 4's model
                # card asks for it in tool-call turns. Decided by where the request goes, not by the chat's
                # provider: a llama.cpp chat also sends to OpenRouter, e.g. for the web search. OpenRouter gets
                # reasoning_details instead; earlier questions get neither.
                wire["reasoning_content"] = thinking
            messages.append(wire)
            images = []
            for call in calls:
                if self.control:
                    self.control.check_cancelled()
                function = call.get("function") or {}
                name, arguments = function.get("name", ""), function.get("arguments") or "{}"
                notify("tool_call", describe_call(name, arguments))
                result = run_tool(name, arguments)
                if self.control:
                    self.control.check_cancelled()
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
    return {key: message[key] for key in WIRE_KEYS if key in message}


def _merge_detail(details: dict[tuple[Any, Any], dict[str, Any]], detail: dict[str, Any]) -> None:
    """Joins the streamed pieces of one reasoning detail, which share its index and type."""
    key = (detail.get("index"), detail.get("type"))
    if key not in details:
        details[key] = dict(detail)
        return
    merged = details[key]
    for name, value in detail.items():
        if isinstance(value, str) and name in ("text", "summary", "data"):
            merged[name] = (merged.get(name) or "") + value
        elif value is not None and merged.get(name) is None:
            # E.g. a signature that comes after a first piece with "signature": null; Gemini needs it back.
            merged[name] = value


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
        finish_reason=str(data["choices"][0].get("finish_reason") or ""),
    )


def _optional_sum(values: Any) -> int | None:
    numbers = [int(value) for value in values if value is not None]
    return sum(numbers) if numbers else None
