"""Turns a stored chat into the lines of the Chat history list."""

from __future__ import annotations

from typing import Any

from .session_store import TRANSCRIPT_DONE, TRANSCRIPT_FAILED, TRANSCRIPT_NONE
from .tools import describe_call


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
    entries = [(f"You: {initial_question}",) * 2]
    note = transcript_entry(transcript_state, transcript)
    if note:
        entries.append(note)
    return entries + [(item, item) for item in _message_items(history)]


def _message_items(history: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for message in history:
        content = message.get("content")
        if message["role"] == "user":
            if isinstance(content, list):
                # An image the model asked for; show its description, not the image data.
                text = " ".join(part.get("text", "") for part in content if part.get("type") == "text")
                items.append(f"Image: {text}")
            else:
                items.append(f"You: {content}")
        elif message["role"] == "tool":
            items.append(f"Tool result: {message.get('caption') or content}")
        elif message.get("tool_calls"):
            # Text the model wrote before calling tools; the final answer is the last "Model:" item.
            if content:
                items.append(f"Model note: {content}")
            for call in message["tool_calls"]:
                function = call.get("function") or {}
                items.append(f"Tool call: {describe_call(function.get('name', ''), function.get('arguments') or '')}")
        else:
            sources = "".join(f"\n- {source['title']}: {source['url']}" for source in message.get("sources") or [])
            items.append(f"Model: {content}" + (f"\n\nSources:{sources}" if sources else ""))
            items.append(usage_text(message.get("usage")))
    return items
