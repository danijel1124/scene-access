"""Turns a stored chat into the lines of the Chat history list."""

from __future__ import annotations

from typing import Any

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


def chat_history_items(initial_question: str, history: list[dict[str, Any]]) -> list[str]:
    items = [f"You: {initial_question}"]
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
