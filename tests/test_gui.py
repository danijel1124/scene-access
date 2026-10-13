import wx

from sceneaccess.config import AppConfig
from sceneaccess.gui import SettingsDialog, cache_status_text


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


def test_cache_status_reports_actual_cached_tokens():
    assert cache_status_text(9137, 9175) == "Cache hit confirmed: 9,137 of 9,175 prompt tokens"


def test_cache_status_does_not_claim_hit_when_provider_reported_none():
    assert cache_status_text(0, 9175) == "No cache hit was confirmed. Processing the video again may incur costs."
