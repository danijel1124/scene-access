from sceneaccess.chat_history import chat_history_entries, usage_text


def chat_history_items(*arguments):
    """The shown text of each item, as the Chat history list displays it."""
    return [shown for shown, _full in chat_history_entries(*arguments)]


def test_the_transcript_item_is_short_and_the_reader_gets_the_full_text():
    transcript = "\n".join(f"[00:{second:02d}] Line {second}." for second in range(40))
    entries = chat_history_entries("Describe it.", [], "done", transcript)

    shown, full = entries[1]
    assert shown.startswith("Transcript: 40 lines of automatic speech recognition")
    assert "[00:39] Line 39." not in shown and full.endswith("[00:39] Line 39.")
    assert chat_history_items("?", [], "done", "")[1] == "Transcript: no speech was detected in the video."
    assert chat_history_items("?", [], "failed")[1].startswith("Transcript: speech recognition failed")
    assert chat_history_items("?", [], "pending") == ["You: ?"]


def test_usage_text_reports_hit_miss_and_write_with_the_answering_model():
    usage = {"model": "z-ai/glm-4.5v", "prompt_tokens": 9175, "cached_tokens": 9137, "cache_write_tokens": 0}

    assert usage_text(usage) == (
        "Usage: model z-ai/glm-4.5v | prompt tokens: 9,175 | cache read (hit): 9,137"
        " | not from cache (miss): 38 | cache write: 0"
    )


def test_usage_text_names_the_provider_whose_cache_was_used():
    usage = {"model": "z-ai/glm-5.3-flash", "provider": "CoreWeave", "prompt_tokens": 9138, "cached_tokens": 8448,
             "cache_write_tokens": 0}

    assert usage_text(usage).startswith("Usage: model z-ai/glm-5.3-flash via CoreWeave | prompt tokens: 9,138")


def test_usage_text_does_not_claim_zero_hits_when_provider_reported_nothing():
    usage = {"model": "openrouter/auto", "prompt_tokens": 9175, "cached_tokens": None, "cache_write_tokens": None}

    assert usage_text(usage) == (
        "Usage: model openrouter/auto | prompt tokens: 9,175 | cache details were not reported by the provider"
    )


def test_chat_history_lists_each_message_with_usage_below_every_answer():
    history = [
        {"role": "assistant", "content": "A jacket.", "usage": {"model": "m", "prompt_tokens": 10, "cached_tokens": 0, "cache_write_tokens": 10}},
        {"role": "user", "content": "Is it glossy?"},
        {"role": "assistant", "content": "No."},
    ]

    assert chat_history_items("Describe it.", history) == [
        "You: Describe it.",
        "Model: A jacket.",
        "Usage: model m | prompt tokens: 10 | cache read (hit): 0 | not from cache (miss): 10 | cache write: 10",
        "You: Is it glossy?",
        "Model: No.",
        "Usage: no data was recorded for this answer.",
    ]


def test_chat_history_shows_tool_steps_readably_and_ends_with_the_answer():
    items = chat_history_items("Brand?", [
        {"role": "assistant", "content": "Let me look closer.", "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "zoom_frame", "arguments": '{"time": "02:51"}'}}]},
        {"role": "tool", "tool_call_id": "c", "content": "long text", "caption": "Image of 02:51, the whole frame"},
        {"role": "user", "content": [{"type": "text", "text": "Image of 02:51."},
                                     {"type": "image_url", "image_url": {"url": "session-file:f.jpg"}}]},
        {"role": "assistant", "content": "It is a Moncler.", "sources": [{"title": "Moncler", "url": "https://m.example"}],
         "usage": {"model": "m", "prompt_tokens": 10, "cached_tokens": 8, "cache_write_tokens": 0, "requests": 2}},
    ])
    assert items[:5] == ["You: Brand?", "Model note: Let me look closer.", "Tool call: Zoom into 02:51",
                         "Tool result: Image of 02:51, the whole frame", "Image: Image of 02:51."]
    assert items[5] == "Model: It is a Moncler.\n\nSources:\n- Moncler: https://m.example"
    assert "requests: 2" in items[6]
