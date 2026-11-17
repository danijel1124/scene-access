"""The sound of a chat's video: whether the model answering hears it, a timestamped transcript for one that
does not, and the sound as a track of its own for a model on a llama.cpp server, which ignores a video's sound."""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .models import AUTO_ROUTER, ModelCatalog
from .providers import LLAMA_CPP
from .session_store import NO_SOUND_FILE, SOUND_FILE_PREFIX, TRANSCRIBE_AUTO, TRANSCRIBE_ON, VideoSession
from .tools import format_time, local_video, run_ffmpeg
from .request_control import RequestControl

# Audio is sent in pieces of this length, so long videos stay within request size limits.
CHUNK_SECONDS = 600
# A new line starts after a pause this long, and a line never spans more than MAX_LINE_SECONDS.
PAUSE_SECONDS = 1.5
MAX_LINE_SECONDS = 15.0


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


def sound_parts(session: VideoSession, control: RequestControl | None = None, *, source: Path | None = None,
                prefix: str = SOUND_FILE_PREFIX) -> list[dict[str, Any]]:
    """The video's sound as input_audio parts for a model on a llama.cpp server; empty if the video has none.
    Extracted once and kept beside the chat's video, so every request sends the same and stays cached.
    source and prefix give a section's own sound, from its file and under its own names."""
    folder = session.directory
    stored = sorted(folder.glob(f"{prefix}*.mp3"))
    no_sound = folder / (NO_SOUND_FILE if prefix == SOUND_FILE_PREFIX else f"{prefix}none")
    if not stored and not no_sound.exists():
        with (nullcontext(source) if source else local_video(session)) as video, \
                tempfile.TemporaryDirectory() as temporary:
            chunks = extract_audio_chunks(video, Path(temporary), **({"control": control} if control else {}))
            for index, (_start, chunk) in enumerate(chunks):
                target = folder / f"{prefix}{index:03d}.mp3"
                # Copied under another name first, so a failed copy never looks like a stored piece.
                partial = target.with_suffix(".tmp")
                shutil.copyfile(chunk, partial)
                os.chmod(partial, 0o600)
                partial.replace(target)
        if not chunks:
            no_sound.touch()
        stored = sorted(folder.glob(f"{prefix}*.mp3"))
    return [{"type": "input_audio", "input_audio": {"data": base64.b64encode(path.read_bytes()).decode("ascii"),
                                                    "format": "mp3"}} for path in stored]


def with_sound(messages: list[dict[str, Any]], parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """messages with the sound parts right after the video, in the message that carries it."""
    result, placed = [], not parts
    for message in messages:
        content = message.get("content")
        if not placed and isinstance(content, list):
            videos = [index for index, part in enumerate(content) if part.get("type") == "video_url"]
            if videos:
                message = {**message, "content": [*content[:videos[0] + 1], *parts, *content[videos[0] + 1:]]}
                placed = True
        result.append(message)
    return result


def extract_audio_chunks(video: Path, folder: Path, chunk_seconds: int | None = None,
                         control: RequestControl | None = None) -> list[tuple[float, Path]]:
    """Writes the sound as mono MP3 pieces and returns (start second, file); empty if there is no sound."""
    chunk_seconds = chunk_seconds or CHUNK_SECONDS
    completed = run_ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", "-f", "segment",
                            "-segment_time", str(chunk_seconds), "-reset_timestamps", "1", "-y",
                            str(folder / "audio_%03d.mp3")], timeout=1800,
                            **({"control": control} if control else {}))
    chunks = sorted(path for path in folder.glob("audio_*.mp3") if path.stat().st_size > 0)
    if completed.returncode != 0:
        if not chunks and ("does not contain any stream" in completed.stderr or "matches no streams" in completed.stderr):
            return []
        raise RuntimeError(completed.stderr.strip() or "ffmpeg could not read the sound of the video.")
    return [(float(index * chunk_seconds), path) for index, path in enumerate(chunks)]


def transcript_lines(words: list[dict[str, Any]]) -> list[str]:
    """Groups timed words into lines like "[01:23] What was said."."""
    lines: list[str] = []
    current: list[str] = []
    start = last_end = 0.0

    def flush() -> None:
        if current:
            lines.append(f"[{format_time(int(start))}] {' '.join(current)}")
            current.clear()

    for word in words:
        text = str(word.get("word", "")).strip()
        if not text:
            continue
        begin = float(word.get("start") or 0)
        if current and (begin - last_end > PAUSE_SECONDS or begin - start > MAX_LINE_SECONDS):
            flush()
        if not current:
            start = begin
        current.append(text)
        last_end = float(word.get("end") or begin)
        if text.endswith((".", "?", "!")):
            flush()
    flush()
    return lines


def _timed_words(result: dict[str, Any], offset: float) -> list[dict[str, Any]]:
    words = result.get("words") or []
    if not words:
        # Without word times, fall back to segments, and to the plain text at the start of the piece.
        words = [{"word": segment.get("text", ""), "start": segment.get("start", 0), "end": segment.get("end", 0)}
                 for segment in result.get("segments") or []] or [{"word": result.get("text", ""), "start": 0, "end": 0}]
    return [{**word, "start": float(word.get("start") or 0) + offset, "end": float(word.get("end") or 0) + offset}
            for word in words]


def make_transcript(session: VideoSession, client: Any) -> str:
    """Transcribes the chat's video; returns "" if it has no speech. Raises if transcription fails."""
    control = getattr(client, "control", None)
    if control:
        control.check_cancelled()
    with local_video(session) as video, tempfile.TemporaryDirectory() as folder:
        words = []
        for offset, chunk in extract_audio_chunks(video, Path(folder), **({"control": control} if control else {})):
            if control:
                control.check_cancelled()
            words += _timed_words(client.transcribe(chunk.read_bytes(), "mp3"), offset)
    return "\n".join(transcript_lines(words))
