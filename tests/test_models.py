import json

import pytest

from sceneaccess.models import ModelCatalog, api_url, routing

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
    assert routing("openrouter/auto", True, catalog) == {"plugins": [
        {"id": "auto-router", "allowed_models": ["google/gemini-flash"]}]}
    assert routing("openrouter/auto", False, catalog) == {}
    assert routing("z-ai/glm-flash", True, catalog) == {}


def test_routing_excludes_providers_for_every_model_and_models_only_for_auto():
    catalog = ModelCatalog("https://openrouter.ai/api/v1/chat/completions", opener=lambda request, timeout: FakeResponse())
    assert routing("openrouter/auto", True, catalog, ("google-ai-studio",), ("google/*",)) == {
        "provider": {"ignore": ["google-ai-studio"]},
        "plugins": [{"id": "auto-router", "allowed_models": ["google/gemini-flash"], "excluded_models": ["google/*"]}]}
    assert routing("openrouter/auto", False, catalog, (), ("z-ai/glm-flash",)) == {
        "plugins": [{"id": "auto-router", "excluded_models": ["z-ai/glm-flash"]}]}
    # A chosen model is not replaced by another one, but its providers can still be excluded.
    assert routing("z-ai/glm-flash", False, catalog, ("novita",), ("z-ai/glm-flash",)) == {
        "provider": {"ignore": ["novita"]}}


def test_catalog_lists_providers_by_name_and_all_model_ids():
    providers = {"data": [{"name": "Novita", "slug": "novita"}, {"name": "Google AI Studio", "slug": "google-ai-studio"}]}
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        response = FakeResponse()
        if request.full_url.endswith("/providers"):
            response.read = lambda: json.dumps(providers).encode()
        return response

    catalog = ModelCatalog("https://openrouter.ai/api/v1/chat/completions", opener=opener)
    assert catalog.providers() == [("Google AI Studio", "google-ai-studio"), ("Novita", "novita")]
    assert catalog.providers() == [("Google AI Studio", "google-ai-studio"), ("Novita", "novita")]
    assert catalog.model_ids()[0] == "acme/listen-no-tools"
    assert calls == ["https://openrouter.ai/api/v1/providers", "https://openrouter.ai/api/v1/models"]


def test_a_failed_model_list_means_unknown_not_a_crash():
    def fail(request, timeout):
        raise OSError("offline")

    catalog = ModelCatalog("https://openrouter.ai/api/v1/chat/completions", opener=fail)
    assert catalog.hears_audio("google/gemini-flash") is None
    assert catalog.providers() == [] and catalog.model_ids() == []
    assert routing("z-ai/glm-flash", True, catalog) == {}
    # A chat that relies on hearing models must not be sent to an unlimited openrouter/auto.
    with pytest.raises(RuntimeError, match="model list could not be loaded"):
        routing("openrouter/auto", True, catalog)
