"""The reviewer of a section's description: after the mechanical cleaning (cleaning.py), a model checks the rest in
a request of its own, without the video and without the chat, for only what a model can see: a line that
contradicts the title or the transcript, and a line that only says what a blind listener would guess anyway. The
second kind is deleted; only a contradiction sends the section back to the model with its video, once."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..prompts import FIX_TASK, REVIEW_TASK, REVIEWER_SYSTEM_PROMPT
from ..timecodes import format_time
from .cleaning import TIME_AT_START


@dataclass(frozen=True)
class Reviewer:
    """Who checks the sections' descriptions, when not the model that wrote them: a client, its model and the
    routing for it, e.g. an OpenRouter model for a chat on the user's own server. It gets only text."""
    client: Any
    model: str
    routing: dict[str, Any] | None = None


# The answer is forced into this form by the server's grammar, so the reviewer's reasoning never reads as
# findings (Gemma 4 12B without thinking wrote its reasoning as a list and ended with "Result: OK").
REVIEW_SCHEMA = {"type": "object", "properties": {"problems": {"type": "array", "items": {
    "type": "object", "properties": {"time": {"type": "string"}, "question": {"type": "string", "enum": ["e", "f"]},
                                     "why": {"type": "string"}},
    "required": ["time", "question", "why"]}}}, "required": ["problems"]}
REVIEW_FORMAT = {"response_format": {"type": "json_schema", "json_schema": {"name": "findings", "schema": REVIEW_SCHEMA}}}


def review_messages(text: str, heard: str | None, number: int, start: float, end: float,
                    title: str = "") -> list[dict[str, Any]]:
    """The reviewer's request: its own system prompt, then the part to check. heard is the transcript of the
    part, or None when the chat sends no transcript: then the reviewer must not take speech the description
    mentions for made up."""
    if heard is None:
        heard = "(no transcript is available; the model may have heard the speech itself)"
    part = REVIEW_TASK.format(number=number, start=format_time(round(start)), end=format_time(round(end)),
                              title=title or "(none)", text=text.strip(),
                              heard=heard.strip() or "(nothing is said in this part)")
    return [{"role": "system", "content": REVIEWER_SYSTEM_PROMPT}, {"role": "user", "content": part}]


def reviewer_findings(answer: str) -> list[dict[str, str]]:
    """The problems in a reviewer's JSON answer, each with time, question ("e" or "f") and why. Raises
    ValueError for an answer that is not the JSON asked for, so it never counts as nothing found."""
    try:
        problems = json.loads(answer)["problems"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"The reviewer's answer is not the JSON asked for: {answer[:200]}") from error
    return [{"time": str(problem["time"]).strip(), "question": problem["question"],
             "why": str(problem.get("why", "")).strip()}
            for problem in (problems if isinstance(problems, list) else [])
            if isinstance(problem, dict) and problem.get("question") in ("e", "f")
            and TIME_AT_START.match(str(problem.get("time", "")))]


def fix_request(text: str, contradictions: list[dict[str, str]]) -> str:
    found = "\n".join(f"- {problem['time']}: {problem['why']}" for problem in contradictions)
    return FIX_TASK.format(text=text.strip(), found=found)
