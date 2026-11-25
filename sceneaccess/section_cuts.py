"""A long video's sections as files: whether a video needs them, their bounds, cutting them with ffmpeg once and
reusing the files, a section's own sound, and the transcript lines that fall in a section."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .request_control import RequestControl
from .session_store import SECTION_FILE_PREFIX, VideoSession
from .tools import local_video, run_ffmpeg, video_size_and_duration
from .transcript import sound_parts

# Sections end a little before the limit, as encoding can make a cut a few hundredths of a second longer.
LIMIT_MARGIN = 0.98
TRANSCRIPT_LINE = re.compile(r"^\[(\d+(?::\d{2}){1,2})\]")


@dataclass(frozen=True)
class Section:
    index: int
    start: float
    end: float
    path: Path


def video_duration(session: VideoSession) -> float:
    with local_video(session) as video:
        return video_size_and_duration(video)[1]


def needs_sections(session: VideoSession, duration: float) -> bool:
    """Only a video longer than the chat's section length is described in sections; others go whole."""
    return session.section_seconds > 0 and duration > session.section_seconds


def section_bounds(duration: float, seconds: int) -> list[tuple[float, float]]:
    """Sections of equal length, none longer than seconds. Equal rather than full sections and a short rest: a
    rest of a few seconds would be a part with hardly anything in it, and adding it to the section before would
    make that one longer than the model takes."""
    count = max(1, math.ceil(duration / (seconds * LIMIT_MARGIN)))
    length = duration / count
    return [(index * length, duration if index == count - 1 else (index + 1) * length) for index in range(count)]


def cut_sections(session: VideoSession, duration: float, control: RequestControl | None = None) -> list[Section]:
    """The chat's sections as files beside its video, cut once and then reused. Encoded anew rather than copied:
    copying can only cut at key frames, which would move each section by seconds."""
    sections = []
    for index, (start, end) in enumerate(section_bounds(duration, session.section_seconds)):
        path = session.directory / f"{SECTION_FILE_PREFIX}{session.section_seconds}s-{index:03d}.mp4"
        if not path.exists():
            # Written under another name first, so a failed cut never looks like a finished section.
            partial = path.with_name(path.stem + ".tmp.mp4")
            with local_video(session) as video:
                done = run_ffmpeg(["-ss", f"{start:.3f}", "-i", str(video), "-t", f"{end - start:.3f}",
                                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                                   "-c:a", "aac", "-b:a", "96k", "-y", str(partial)], timeout=900,
                                  **({"control": control} if control else {}))
            if done.returncode != 0 or not partial.exists():
                partial.unlink(missing_ok=True)
                raise RuntimeError(done.stderr.strip() or f"ffmpeg could not cut section {index + 1}.")
            partial.replace(path)
        sections.append(Section(index, start, end, path))
    return sections


def section_sound(session: VideoSession, section: Section, control: RequestControl | None = None) -> list[dict[str, Any]]:
    """A section's own sound as input_audio parts, for a model on a llama.cpp server that hears."""
    return sound_parts(session, control, source=section.path, prefix=f"{section.path.stem}-sound-")


def transcript_between(transcript: str, start: float, end: float) -> str:
    """The lines of a transcript like "[01:23] What was said" that start within [start, end)."""
    lines = []
    for line in transcript.splitlines():
        found = TRANSCRIPT_LINE.match(line)
        if found:
            parts = [int(part) for part in found.group(1).split(":")]
            seconds = sum(part * 60 ** power for power, part in enumerate(reversed(parts)))
            if start <= seconds < end:
                lines.append(line)
    return "\n".join(lines)
