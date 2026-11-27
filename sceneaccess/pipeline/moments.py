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
from itertools import pairwise
from typing import Any

from ..llm.client import DeltaHandler, EventHandler
from ..prompts import MOMENTS_SYSTEM_PROMPT, MOMENTS_TASK
from ..request_control import RequestCancelled
from ..storage.session_store import VideoSession
from ..timecodes import format_time, parse_time
from .cleaning import TIME_AT_START, time_at_start

_MOMENT = {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"},
                                            "text": {"type": "string"}},
           "required": ["start", "end", "text"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {"moments": {"type": "array", "items": _MOMENT}},
          "required": ["moments"], "additionalProperties": False}
# strict, so that OpenRouter's providers with a strict mode keep to the form exactly.
FORMAT = {"response_format": {"type": "json_schema", "json_schema": {"name": "moments", "strict": True,
                                                                     "schema": SCHEMA}}}
# Marks before a sentence when no time stands there: a list mark or a bold mark. A time with its marks is found
# by cleaning's TIME_AT_START, the one rule for a time at the start of a line, "[00:26]" and "1:17 – Ende" included.
_MARKS = re.compile(r"^(?:[-*•]\s+|\*\*)+")
# A time anywhere in a line: one not at its start, as in "**Scene 2 (00:26–00:40)**", leaves the line's time unknown.
_ANY_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
# Where a sentence ends, within a line.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_into_moments(client: Any, session: VideoSession, text: str, duration: float, routing: dict[str, Any], *,
                       llama_cpp: bool, on_event: EventHandler | None = None,
                       on_delta: DeltaHandler | None = None) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Asks session's model to split text, the finished answer about a video of duration seconds, into moments.
    Returns them, or None when the answer did not keep to the form, and the request's usage, None if it failed.
    A cancel is raised; any other failure means no moments."""
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
    the video, every sentence of a moment found in text as it is, in order, and a moment starting at the time text
    gives where its first sentence stands, from its own line or a heading above. Else None.

    Every sentence of text then belongs to a moment, in its order there: one the model left out goes with the
    moment before it, or into a moment of its own if a later time of its own stands before it, and one before the
    first moment into a moment from 00:00, so an audio track leaves nothing out and no word is changed. A moment's text is its sentences without the times and marks before them,
    which would otherwise be read out."""
    try:
        moments = json.loads(answer)["moments"]
        times = [(parse_time(moment["start"]), parse_time(moment["end"])) for moment in moments]
        asked = [_sentences(str(moment["text"])) for moment in moments]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if not moments:
        return None
    sentences = _answer_sentences(text)
    owner: dict[int, int] = {}
    position, last = 0, 0.0
    for number, ((start, end), parts) in enumerate(zip(times, asked, strict=True)):
        if not parts or start > end or start < last or end > duration + 1:
            return None
        first = None
        for part in parts:
            # Searched on from where the moment before stood, so a short sentence said twice is found at its place;
            # contained rather than equal, as a model may leave out a remnant before it, e.g. "– Ende:".
            while position < len(sentences) and part not in sentences[position][1]:
                position += 1
            if position == len(sentences):
                return None
            owner[position] = number
            first = position if first is None else first
            position += 1
        # A moment given the time of the one before or after it would be read out at the wrong time.
        given = sentences[first][0]
        if given is not None and abs(given - start) > 1:
            return None
        last = start
    built: list[dict[str, Any]] = []
    given_moments: dict[int, dict[str, Any]] = {}
    for index, (time, sentence, heading) in enumerate(sentences):
        if index in owner:
            number = owner[index]
            if number not in given_moments:
                given_moments[number] = {"start": times[number][0], "end": times[number][1], "text": []}
                built.append(given_moments[number])
            target = given_moments[number]
        elif heading:
            continue
        else:
            target = built[-1] if built else None
            # An added moment has no end yet: it reaches at least to its own start.
            reach = target and max(target["start"] if target["end"] is None else target["end"], target["start"] + 1)
            if time is not None and (target is None or time >= reach):
                # A stretch the model left out, with a time of its own after the moment before: its own moment.
                target = {"start": time, "end": None, "text": []}
                built.append(target)
            elif target is None:
                target = {"start": 0.0, "end": None, "text": []}
                built.append(target)
        target["text"].append(sentence)
    # An added moment ends where the next one starts, the last one with the video.
    for moment, after in zip(built, [*built[1:], None], strict=True):
        if moment["end"] is None:
            moment["end"] = after["start"] if after else max(duration, moment["start"])
    if any(after["start"] < moment["start"] for moment, after in pairwise(built)):
        return None
    return [{**moment, "text": " ".join(moment["text"])} for moment in built]


def _answer_sentences(text: str) -> list[tuple[float | None, str, bool]]:
    """The sentences of text in order, each as compared, with the time it is about: its line's own time at its
    start, else the last one before it, e.g. from a heading, unknown (None) before the first time and after a line
    with a time elsewhere in it; and whether it is a heading, a line in bold only, which no audio track reads."""
    found, time = [], None
    for line in text.splitlines():
        line = line.strip()
        own = time_at_start(line)
        if own is not None:
            time = float(own)
        elif _ANY_TIME.search(line):
            time = None
        heading = len(line) > 4 and line.startswith("**") and line.endswith("**") and "**" not in line[2:-2]
        found += [(time, sentence, heading) for sentence in _sentences(line)]
    return found


def _sentences(text: str) -> list[str]:
    """The sentences of text, each without the times and marks before it and with single spaces, as compared."""
    found = []
    for line in text.splitlines():
        for sentence in _SENTENCE_END.split(line.strip()):
            sentence = _MARKS.sub("", TIME_AT_START.sub("", sentence.strip(), count=1)).replace("**", "")
            sentence = " ".join(sentence.split())
            if any(character.isalpha() for character in sentence):
                found.append(sentence)
    return found
