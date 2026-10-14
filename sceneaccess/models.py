"""What OpenRouter's models accept, from its public model list."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable
from urllib.request import Request, urlopen

AUTO_ROUTER = "openrouter/auto"


def api_url(base_url: str, path: str) -> str:
    """Another endpoint of the configured API, e.g. "models" next to ".../chat/completions"."""
    if base_url.endswith("/chat/completions"):
        return base_url[: -len("chat/completions")] + path
    return "https://openrouter.ai/api/v1/" + path


class ModelCatalog:
    """Fetches the model list once and answers questions about it; None means "unknown"."""

    def __init__(self, base_url: str, opener: Callable[..., Any] = urlopen):
        self.url = api_url(base_url, "models")
        self.opener = opener
        self._models: dict[str, dict[str, Any]] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._models is None:
                try:
                    with self.opener(Request(self.url), timeout=30) as response:
                        data = json.loads(response.read().decode("utf-8"))
                    self._models = {model["id"]: model for model in data.get("data", [])}
                except Exception:
                    # Without the list nothing is known; try again on the next question.
                    return {}
            return self._models

    def hears_audio(self, model: str) -> bool | None:
        """Whether the model gets the sound of a video; None if unknown, e.g. for openrouter/auto."""
        if model == AUTO_ROUTER:
            return None
        info = self._load().get(model)
        if info is None:
            return None
        return "audio" in (info.get("architecture") or {}).get("input_modalities", [])

    def audio_video_tool_models(self) -> list[str]:
        """Models that take video with its sound and support tools, for openrouter/auto to choose from."""
        chosen = []
        for model_id, info in self._load().items():
            inputs = (info.get("architecture") or {}).get("input_modalities", [])
            # Variants like ":batch" and other routers are not models of their own.
            if (":" in model_id or "router" in model_id or model_id.startswith("openrouter/")
                    or "tools" not in info.get("supported_parameters", [])):
                continue
            if "video" in inputs and "audio" in inputs:
                chosen.append(model_id)
        return sorted(chosen)


def auto_router_plugins(session_model: str, audio_only: bool, catalog: ModelCatalog) -> list[dict[str, Any]]:
    """The plugin that limits openrouter/auto to models that hear the video, if the chat asks for it."""
    if not audio_only or session_model != AUTO_ROUTER:
        return []
    allowed = catalog.audio_video_tool_models()
    if not allowed:
        # Such a chat has no transcript, because only hearing models may answer it. Unlimited,
        # openrouter/auto could pick a model that gets neither the sound nor a transcript.
        raise RuntimeError("OpenRouter's model list could not be loaded, so openrouter/auto cannot be limited "
                           "to models that hear the video. Please try again.")
    return [{"id": "auto-router", "allowed_models": allowed}]
