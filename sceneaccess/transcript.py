"""A timestamped transcript of a video's speech, for models that only see the picture."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .models import AUTO_ROUTER, ModelCatalog
from .session_store import VideoSession
from .tools import format_time, local_video, run_ffmpeg

# Audio is sent in pieces of this length, so long videos stay within request size limits.
CHUNK_SECONDS = 600
# A new line starts after a pause this long, and a line never spans more than MAX_LINE_SECONDS.
PAUSE_SECONDS = 1.5
MAX_LINE_SECONDS = 15.0


def needs_transcript(session: VideoSession, catalog: ModelCatalog) -> bool:
    """True unless the chat's model is known to hear the video's sound."""
    if session.audio_only and session.model == AUTO_ROUTER and catalog.audio_video_tool_models():
        return False
    # Unknown models, and openrouter/auto without the audio-only setting, may not hear; a transcript is safe.
    return catalog.hears_audio(session.model) is not True


def extract_audio_chunks(video: Path, folder: Path, chunk_seconds: int | None = None) -> list[tuple[float, Path]]:
    """Writes the sound as mono MP3 pieces and returns (start second, file); empty if there is no sound."""
    chunk_seconds = chunk_seconds or CHUNK_SECONDS
    completed = run_ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", "-f", "segment",
                            "-segment_time", str(chunk_seconds), "-reset_timestamps", "1", "-y",
                            str(folder / "audio_%03d.mp3")], timeout=1800)
    chunks = sorted(path for path in folder.glob("audio_*.mp3") if path.stat().st_size > 0)
    if completed.returncode != 0 and not chunks:
        if "does not contain any stream" in completed.stderr or "matches no streams" in completed.stderr:
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
    with local_video(session) as video, tempfile.TemporaryDirectory() as folder:
        words = []
        for offset, chunk in extract_audio_chunks(video, Path(folder)):
            words += _timed_words(client.transcribe(chunk.read_bytes(), "mp3"), offset)
    return "\n".join(transcript_lines(words))
