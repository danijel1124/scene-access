"""The second step of a first answer: the finished text split into its moments, each with a start, an end and
its words copied from the text, kept with the answer for an audio track later. A request of its own, without the
video and the tools, whose answer the server's grammar forces into a JSON form; on the user's own server also
without thinking. One step would not do: under the grammar, llama.cpp offered the model no tools, and the model
wrote the moments as a second, different description (probe with Qwen3.5-9B, 25.11.2026). Whatever goes wrong
here only means no moments: the answer stays as it is."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from ..llm.client import DeltaHandler, EventHandler
from ..prompts import MOMENTS_SYSTEM_PROMPT, MOMENTS_TASK
from ..request_control import RequestCancelled
from ..storage.session_store import VideoSession
from ..timecodes import format_time, parse_time

_MOMENT = {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"},
                                            "text": {"type": "string"}},
           "required": ["start", "end", "text"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {"moments": {"type": "array", "items": _MOMENT}},
          "required": ["moments"], "additionalProperties": False}
# strict, so that OpenRouter's providers with a strict mode keep to the form exactly.
FORMAT = {"response_format": {"type": "json_schema", "json_schema": {"name": "moments", "strict": True,
                                                                     "schema": SCHEMA}}}
# What a model kept at the start of a moment's text although asked not to: a list mark, a bold mark, a time or a
# span of times, e.g. "- **00:00** " or "**00:00 - 00:26 (Toothpaste)**".
_LEADING_MARKS = re.compile(r"^(?:[-*•]\s+|\*\*|\(?\d{1,2}:\d{2}(?::\d{2})?\)?(?:\s*[-–]\s*\d{1,2}:\d{2}(?::\d{2})?)?"
                            r"\s*:?\s*)+")


def split_into_moments(client: Any, session: VideoSession, text: str, duration: float, routing: dict[str, Any], *,
                       llama_cpp: bool, on_event: EventHandler | None = None,
                       on_delta: DeltaHandler | None = None) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Asks session's model to split text, the finished answer about a video of duration seconds, into moments.
    Returns them, or None when the answer did not keep to the form, and the request's usage, None if it failed.
    Only cancelling stops the answer; any other failure means no moments."""
    if on_event:
        on_event("moments", "Splitting the answer into moments")
    options = {**routing, **FORMAT}
    if llama_cpp:
        # As for the reviewer: without thinking, which this step does not need.
        options["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        # Only to providers that keep to the form; OpenRouter's thinking stays as it is, as some models must think.
        options["provider"] = {**options.get("provider", {}), "require_parameters": True}
    messages = [{"role": "system", "content": MOMENTS_SYSTEM_PROMPT},
                {"role": "user", "content": MOMENTS_TASK.format(length=format_time(round(duration)), text=text)}]
    try:
        turn = client.run_turn(replace(session, tools=[]), messages, lambda *_: None, None, options,
                               max_tool_rounds=0, on_delta=on_delta)
    except RequestCancelled:
        raise
    except Exception:
        return None, None
    usage = turn.completion.usage_record()
    if turn.completion.cut_off:
        return None, usage
    return parsed_moments(turn.completion.answer, text, duration), usage


def parsed_moments(answer: str, text: str, duration: float) -> list[dict[str, Any]] | None:
    """The moments in answer, with their times in seconds, if all keep to the form: readable times in order and in
    the video, and each moment's words found in text as they are. Else None, and so for no moments at all."""
    try:
        moments = json.loads(answer)["moments"]
    except (ValueError, KeyError, TypeError):
        return None
    found, last = [], 0.0
    whole = _plain(text)
    for moment in moments if isinstance(moments, list) else []:
        try:
            start, end = parse_time(moment["start"]), parse_time(moment["end"])
            words = _LEADING_MARKS.sub("", str(moment["text"]).strip()).strip()
        except (KeyError, TypeError, ValueError):
            return None
        if words.endswith("**"):
            words = words[:-2].rstrip()
        if not words or _plain(words) not in whole or start > end or start < last or end > duration + 1:
            return None
        found.append({"start": start, "end": end, "text": words})
        last = start
    return found or None


def _plain(text: str) -> str:
    """For comparing words only: without bold marks, with every run of spaces and line breaks as one space."""
    return " ".join(text.replace("**", "").split())
