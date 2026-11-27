"""The sound of a chat's video: a timestamped transcript of its speech, and the sound as a track of its own for a
model on a llama.cpp server, which ignores a video's sound. Whether a model hears it is hearing.py."""

from __future__ import annotations

import base64
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from ..request_control import RequestControl
from ..storage.session_store import NO_SOUND_FILE, SOUND_FILE_PREFIX, VideoSession
from ..timecodes import format_time
from .video import local_video, run_ffmpeg

# Audio is sent in pieces of this length, so long videos stay within request size limits.
CHUNK_SECONDS = 600
# A new line starts after a pause this long, and a line never spans more than MAX_LINE_SECONDS.
PAUSE_SECONDS = 1.5
MAX_LINE_SECONDS = 15.0


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
                partial.chmod(0o600)
                partial.replace(target)
        if not chunks:
            no_sound.touch()
        stored = sorted(folder.glob(f"{prefix}*.mp3"))
    return [{"type": "input_audio", "input_audio": {"data": base64.b64encode(path.read_bytes()).decode("ascii"),
                                                    "format": "mp3"}} for path in stored]


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
