"""Whether the model answering a chat hears its video's sound, whether the chat's transcript mode then asks for a
transcript, and where the sound goes in a request: after the text of the message with the video, as Gemma 4's model
card asks. The sound itself and the transcript are transcript.py."""

from __future__ import annotations

from typing import Any

from .models import AUTO_ROUTER, ModelCatalog
from .providers import LLAMA_CPP
from .session_store import TRANSCRIBE_AUTO, TRANSCRIBE_ON, VideoSession


def model_hears(session: VideoSession, catalog: ModelCatalog) -> bool:
    """True if the chat's model is known to take in the video's sound; catalog is that of the chat's provider.
    Unknown models, and openrouter/auto without the audio-only setting, may not hear. A llama.cpp server ignores
    a video's sound, so its model hears only the track SceneAccess sends along (see sound_parts); the server runs
    one model, which answers whatever name the chat asks for."""
    if session.provider == LLAMA_CPP:
        ids = catalog.model_ids()
        # A list that could not be read would only be asked for again, and a server that is off waited for twice.
        if not ids:
            return False
        return catalog.hears_audio(session.model if session.model in ids or len(ids) != 1 else ids[0]) is True
    return catalog.hears_audio(session.model) is True or (session.model == AUTO_ROUTER and session.audio_only)


def wants_transcript(session: VideoSession, hears: bool) -> bool:
    """Whether the chat's transcript mode asks for a transcript, given whether the model answering now hears."""
    return session.transcribe == TRANSCRIBE_ON or (session.transcribe == TRANSCRIBE_AUTO and not hears)


def with_sound(messages: list[dict[str, Any]], parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """messages with the sound parts at the end of the message that carries the video: after its text, as
    Gemma 4's model card asks."""
    result, placed = [], not parts
    for message in messages:
        content = message.get("content")
        if not placed and isinstance(content, list):
            if any(part.get("type") == "video_url" for part in content):
                message = {**message, "content": [*content, *parts]}
                placed = True
        result.append(message)
    return result
