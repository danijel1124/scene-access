import wx

from sceneaccess.config import AppConfig
from sceneaccess.gui import SettingsDialog, chat_history_items, usage_text


def test_settings_dialog_can_be_constructed_and_returns_its_values():
    app = wx.App(False)
    frame = wx.Frame(None)
    expected = AppConfig(model="openrouter/auto", base_url="https://router.example/api", api_key="secret-for-test-only")
    try:
        dialog = SettingsDialog(frame, expected)
        assert dialog.value() == expected
        dialog.Destroy()
    finally:
        frame.Destroy()
        app.Destroy()


def test_usage_text_reports_hit_miss_and_write_with_the_answering_model():
    usage = {"model": "z-ai/glm-4.5v", "prompt_tokens": 9175, "cached_tokens": 9137, "cache_write_tokens": 0}

    assert usage_text(usage) == (
        "Usage: model z-ai/glm-4.5v | prompt tokens: 9,175 | cache read (hit): 9,137"
        " | not from cache (miss): 38 | cache write: 0"
    )


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
