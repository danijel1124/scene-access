import json

import pytest

from sceneaccess.models import ModelCatalog, api_url, auto_router_plugins

MODELS = {"data": [
    {"id": "google/gemini-flash", "architecture": {"input_modalities": ["text", "image", "audio", "video"]},
     "supported_parameters": ["tools"]},
    {"id": "google/gemini-flash:batch", "architecture": {"input_modalities": ["audio", "video"]}, "supported_parameters": ["tools"]},
    {"id": "acme/listen-no-tools", "architecture": {"input_modalities": ["audio", "video"]}, "supported_parameters": []},
    {"id": "acme/some-router", "architecture": {"input_modalities": ["audio", "video"]}, "supported_parameters": ["tools"]},
    {"id": "z-ai/glm-flash", "architecture": {"input_modalities": ["text", "image", "video"]}, "supported_parameters": ["tools"]},
]}


class FakeResponse:
    def read(self):
        return json.dumps(MODELS).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_api_url_finds_other_endpoints_next_to_chat_completions():
    assert api_url("https://openrouter.ai/api/v1/chat/completions", "models") == "https://openrouter.ai/api/v1/models"
    assert api_url("https://proxy.example/custom", "audio/transcriptions") == "https://openrouter.ai/api/v1/audio/transcriptions"


def test_catalog_knows_which_models_hear_and_lists_only_real_tool_models():
    calls = []
    catalog = ModelCatalog("https://openrouter.ai/api/v1/chat/completions",
                           opener=lambda request, timeout: calls.append(request.full_url) or FakeResponse())
    assert catalog.hears_audio("google/gemini-flash") is True
    assert catalog.hears_audio("z-ai/glm-flash") is False
    assert catalog.hears_audio("unknown/model") is None
    assert catalog.hears_audio("openrouter/auto") is None
    assert catalog.audio_video_tool_models() == ["google/gemini-flash"]
    assert calls == ["https://openrouter.ai/api/v1/models"]  # fetched once
    assert auto_router_plugins("openrouter/auto", True, catalog) == [
        {"id": "auto-router", "allowed_models": ["google/gemini-flash"]}]
    assert auto_router_plugins("openrouter/auto", False, catalog) == []
    assert auto_router_plugins("z-ai/glm-flash", True, catalog) == []


def test_a_failed_model_list_means_unknown_not_a_crash():
    def fail(request, timeout):
        raise OSError("offline")

    catalog = ModelCatalog("https://openrouter.ai/api/v1/chat/completions", opener=fail)
    assert catalog.hears_audio("google/gemini-flash") is None
    assert auto_router_plugins("z-ai/glm-flash", True, catalog) == []
    # A chat that relies on hearing models must not be sent to an unlimited openrouter/auto.
    with pytest.raises(RuntimeError, match="model list could not be loaded"):
        auto_router_plugins("openrouter/auto", True, catalog)
