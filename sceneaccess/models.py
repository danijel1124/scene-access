"""What OpenRouter's models accept, from its public model list."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

AUTO_ROUTER = "openrouter/auto"


def api_url(base_url: str, path: str) -> str:
    """Derive a sibling endpoint without changing the configured origin or API prefix.

    Only an explicit HTTP(S) chat/completions URL is unambiguous. Queries,
    fragments and credentials are rejected, never silently dropped or forwarded.
    """
    message = ("Invalid API URL: use an absolute http(s) URL ending in /chat/completions "
               "(an optional trailing slash is allowed), without credentials, query or fragment.")
    if (not isinstance(base_url, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in base_url)
            or any(c in base_url for c in "?#\\")):
        raise ValueError(message)
    try:
        parsed = urlsplit(base_url)
        port = parsed.port  # Validate the port, even though its spelling is preserved.
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.netloc.endswith(":") or port == 0):
            raise ValueError(message)
    except ValueError:
        raise ValueError(message) from None
    api_path = parsed.path.rstrip("/")
    if (not api_path.endswith("/chat/completions") or "%" in api_path
            or any(part in ("", ".", "..") for part in api_path.split("/")[1:])):
        raise ValueError(message)
    if (not isinstance(path, str) or not path
            or any(not part or not all(c.isascii() and (c.isalnum() or c in "_-") for c in part)
                   for part in path.split("/"))):
        raise ValueError("Invalid API endpoint: use a relative path such as models or audio/transcriptions.")
    return urlunsplit((parsed.scheme, parsed.netloc, api_path[:-len("chat/completions")] + path, "", ""))


class ModelCatalog:
    """Fetches the model list once and answers questions about it; None means "unknown"."""

    def __init__(self, base_url: str, opener: Callable[..., Any] = urlopen):
        self.url = api_url(base_url, "models")
        self.providers_url = api_url(base_url, "providers")
        self.opener = opener
        self._models: dict[str, dict[str, Any]] | None = None
        self._providers: list[tuple[str, str]] | None = None
        self._lock = threading.Lock()

    def _fetch(self, url: str) -> list[dict[str, Any]]:
        with self.opener(Request(url), timeout=30) as response:
            return json.loads(response.read().decode("utf-8")).get("data", [])

    def _load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._models is None:
                try:
                    self._models = {model["id"]: model for model in self._fetch(self.url)}
                except Exception:
                    # Without the list nothing is known; try again on the next question.
                    return {}
            return self._models

    def model_ids(self) -> list[str]:
        return sorted(self._load())

    def providers(self) -> list[tuple[str, str]]:
        """OpenRouter's providers as (name, slug), sorted by name; empty if the list is unavailable."""
        with self._lock:
            if self._providers is None:
                try:
                    found = [(str(item.get("name") or item["slug"]), str(item["slug"])) for item in self._fetch(self.providers_url)]
                except Exception:
                    return []
                self._providers = sorted(found, key=lambda provider: provider[0].lower())
            return self._providers

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


def routing(session_model: str, audio_only: bool, catalog: ModelCatalog, ignored_providers: tuple[str, ...] = (),
            excluded_models: tuple[str, ...] = ()) -> dict[str, Any]:
    """Request fields that choose who answers: the providers never to use, and for openrouter/auto the
    models it may not pick and, if the chat asks for it, only models that hear the video."""
    options: dict[str, Any] = {}
    if ignored_providers:
        options["provider"] = {"ignore": list(ignored_providers)}
    if session_model != AUTO_ROUTER:
        return options
    plugin: dict[str, Any] = {"id": "auto-router"}
    if audio_only:
        allowed = catalog.audio_video_tool_models()
        if not allowed:
            # Such a chat has no transcript, because only hearing models may answer it. Unlimited,
            # openrouter/auto could pick a model that gets neither the sound nor a transcript.
            raise RuntimeError("OpenRouter's model list could not be loaded, so openrouter/auto cannot be limited "
                               "to models that hear the video. Please try again.")
        plugin["allowed_models"] = allowed
    if excluded_models:
        # Patterns such as "google/*" work here too; an excluded model is never picked, even if allowed.
        plugin["excluded_models"] = list(excluded_models)
    if len(plugin) > 1:
        options["plugins"] = [plugin]
    return options
