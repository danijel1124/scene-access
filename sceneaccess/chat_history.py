"""Turns a stored chat into the lines of the Chat history list."""

from __future__ import annotations

from typing import Any

from .session_store import EXCLUDED, TRANSCRIPT_DONE, TRANSCRIPT_ENTRY, TRANSCRIPT_FAILED, TRANSCRIPT_NONE
from .tools import describe_call, format_time

# Said first for every item of a round hidden from the model, so the screen reader reads it.
HIDDEN_FROM_MODEL = "[hidden from model] "
# What an item of the Chat history shows; the newest ANSWER is selected first.
QUESTION, TRANSCRIPT, ANSWER, USAGE, NOTE, TOOL, IMAGE, THINKING, SECTION = (
    "question", "transcript", "answer", "usage", "note", "tool", "image", "thinking", "section")
# A thinking item shows only its beginning, as a long one would be read out in full when selected.
THINKING_PREVIEW_WORDS = 12


def usage_text(usage: dict[str, Any] | None) -> str:
    if not usage:
        return "Usage: no data was recorded for this answer."
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    cached_tokens = usage.get("cached_tokens")
    cache_write_tokens = usage.get("cache_write_tokens")
    if cached_tokens is None:
        cache = "cache details were not reported by the provider"
    else:
        write = "not reported" if cache_write_tokens is None else f"{cache_write_tokens:,}"
        cache = (f"cache read (hit): {cached_tokens:,} | not from cache (miss): {max(prompt_tokens - cached_tokens, 0):,}"
                 f" | cache write: {write}")
    extras = ""
    if int(usage.get("requests") or 1) > 1:
        extras += f" | requests: {usage['requests']} (with tool calls, cached tokens are counted in each request)"
    if usage.get("web_searches"):
        extras += f" | web searches: {usage['web_searches']}"
    provider = f" via {usage['provider']}" if usage.get("provider") else ""
    return f"Usage: model {usage.get('model') or 'unknown'}{provider} | prompt tokens: {prompt_tokens:,} | {cache}{extras}"


def transcript_entry(state: str, transcript: str) -> tuple[str, str] | None:
    """The item about the transcript: a short line (a long one would be read out in full when selected),
    and the full text for the reader."""
    if state == TRANSCRIPT_DONE and transcript:
        lines = transcript.count("\n") + 1
        short = (f"Transcript: {lines} lines of automatic speech recognition, sent with the video because the "
                 "model cannot hear it. It may contain errors; Enter shows it.")
        return short, "Transcript (automatic speech recognition, may contain errors):\n\n" + transcript
    if state == TRANSCRIPT_DONE:
        text = "Transcript: no speech was detected in the video."
        return text, text
    if state == TRANSCRIPT_FAILED:
        if transcript:
            text = 'Transcript: ' + transcript
            return text, text
        text = ("Transcript: speech recognition failed, so the model answered without it. "
                "Change prompt profile and tools tries again.")
        return text, text
    return None


def chat_history_entries(initial_question: str, history: list[dict[str, Any]], transcript_state: str = TRANSCRIPT_NONE,
                         transcript: str = "") -> list[tuple[str, str]]:
    """Each item as (shown text, text for the reader); they differ only for the transcript."""
    return [(shown, full) for shown, full, _index, _kind in
            chat_history_rows(initial_question, history, transcript_state, transcript)]


def chat_history_rows(initial_question: str, history: list[dict[str, Any]], transcript_state: str = TRANSCRIPT_NONE,
                      transcript: str = "", sections: dict[str, Any] | None = None
                      ) -> list[tuple[str, str, int | None, str]]:
    """Each item as (shown text, text for the reader, index of its history entry or None, kind). sections is a
    chat's progress of describing its video in sections (see sections.py)."""
    rows: list[tuple[str, str, int | None, str]] = [(f"You: {initial_question}", f"You: {initial_question}", None,
                                                     QUESTION)]
    note = transcript_entry(transcript_state, transcript)
    if note:
        rows.append((*note, None, TRANSCRIPT))
    rows += [(shown, full, None, SECTION) for shown, full in section_items(sections, answered=bool(history))]
    for index, message in enumerate(history):
        marker = HIDDEN_FROM_MODEL if message.get(EXCLUDED) else ""
        rows += [(marker + shown, marker + full, index, kind) for shown, full, kind in _message_items(message)]
    return rows


def _message_items(message: dict[str, Any]) -> list[tuple[str, str, str]]:
    """The items of one stored message: shown text, text for the reader and kind."""
    content = message.get("content")
    if message.get(TRANSCRIPT_ENTRY):
        lines = content.count("\n") - 1
        return [(f"Transcript added later: {lines} lines of automatic speech recognition, sent after the video. It "
                 "may contain errors; Enter shows it.", content, TRANSCRIPT)]
    if message["role"] == "user":
        if isinstance(content, list):
            # An image the model asked for; show its description, not the image data.
            text = " ".join(part.get("text", "") for part in content if part.get("type") == "text")
            return [_same(f"Image: {text}", IMAGE)]
        return [_same(f"You: {content}", QUESTION)]
    if message["role"] == "tool":
        return [_same(f"Tool result: {message.get('caption') or content}", TOOL)]
    thinking = [thinking_item(message["thinking"])] if message.get("thinking") else []
    if message.get("tool_calls"):
        # Text the model wrote before calling tools; the final answer is the last "Model:" item.
        items = thinking + ([_same(f"Model note: {content}", NOTE)] if content else [])
        for call in message["tool_calls"]:
            function = call.get("function") or {}
            items.append(_same(f"Tool call: {describe_call(function.get('name', ''), function.get('arguments') or '')}",
                               TOOL))
        return items
    sources = "".join(f"\n- {source['title']}: {source['url']}" for source in message.get("sources") or [])
    return [*thinking, _same(f"Model: {content}" + (f"\n\nSources:{sources}" if sources else ""), ANSWER),
            _same(usage_text(message.get("usage")), USAGE)]


def section_items(sections: dict[str, Any] | None, answered: bool = True) -> list[tuple[str, str]]:
    """The descriptions of a video described in sections, folded like the thinking: each section's beginning in
    the list, all of it for the reader. Before the answer is stored, they are what a stopped description kept."""
    described = (sections or {}).get("sections") or []
    state = "" if answered else ", unfinished"
    items = []
    for entry in described:
        span = f"{format_time(round(entry['start']))} to {format_time(round(entry['end']))}"
        review = entry.get("review")
        checked = "" if review is None else (", described again after the check" if review.get("found")
                                             else ", checked")
        label = f"Section {entry['index'] + 1} ({span}{checked}{state})"
        items.append(_folded(label, entry["text"]))
        if review and review.get("found"):
            items.append(_folded(f"Check of section {entry['index'] + 1}, found",
                                 f"{review['found']}\n\nThe first description:\n\n{review.get('first_text', '')}"))
    if answered and (sections or {}).get("joined"):
        items.append(_folded("Sections joined without the model", sections["joined"]))
    return items


def _folded(label: str, text: str) -> tuple[str, str]:
    words = text.split()
    beginning = " ".join(words[:THINKING_PREVIEW_WORDS]) + (" …" if len(words) > THINKING_PREVIEW_WORDS else "")
    return f"{label}, Enter shows it: {beginning}", f"{label}:\n\n{text.strip()}"


def thinking_item(thinking: str, label: str = "Model's thinking") -> tuple[str, str, str]:
    """The model's reasoning, folded: its beginning in the list, all of it for the reader (Enter)."""
    words = thinking.split()
    beginning = " ".join(words[:THINKING_PREVIEW_WORDS]) + (" …" if len(words) > THINKING_PREVIEW_WORDS else "")
    return (f"{label} ({len(words)} words; Enter shows it): {beginning}", f"{label}:\n\n{thinking.strip()}", THINKING)


def _same(text: str, kind: str) -> tuple[str, str, str]:
    return text, text, kind
