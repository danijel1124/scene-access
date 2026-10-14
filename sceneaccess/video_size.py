"""How large a video is once sent, and shrinking it with ffmpeg so that a provider accepts it."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Google AI Studio rejects requests over 20,000,000 bytes (measured October 2026); the rest is left for
# the prompt, the transcript and the chat history, which every request sends along with the video.
SIZE_LIMIT = 19_000_000
# Lower than the limit: every image a tool returns stays in the chat and is sent again with each later
# question. 4 MB hold about ten high-resolution frames of a detailed picture (measured up to 470 kB each).
DEFAULT_SHRINK_TARGET_MB = 16


class ShrinkCancelled(Exception):
    """Raised when the user cancelled shrinking."""


def base64_size(file_size: int) -> int:
    """Bytes of the Base64 text that carries a file of file_size bytes in a request."""
    return (file_size + 2) // 3 * 4


def megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def next_lower_height(requested: int, actual: int, choices: tuple[int, ...]) -> int | None:
    """The next quality step below a download. Starting from the smaller of the requested and the actual
    height, the step always gets smaller, even if the site had no format at the requested height."""
    current = min(requested, actual) if actual else requested
    lower = [height for height in choices if height < current]
    return max(lower) if lower else None


def video_height(path: Path) -> int:
    """The picture height of a video file, 0 if unknown."""
    from .tools import video_size_and_duration

    try:
        return int(video_size_and_duration(path)[0][1])
    except Exception:
        return 0


@dataclass(frozen=True)
class ShrinkPlan:
    video_kbps: int
    audio_kbps: int
    max_height: int
    fps: int | None  # None keeps the original frame rate


def shrink_plan(target_base64: int, duration: float) -> ShrinkPlan:
    """Bitrates and picture size that fit a video of duration seconds into target_base64 bytes once sent."""
    # Base64 adds a third; keep some room for the MP4 container and bitrate overshoot.
    total_kbps = target_base64 * 3 / 4 * 0.92 * 8 / max(duration, 1) / 1000
    # Stereo stays: for many videos, such as ASMR, the sound matters as much as the picture.
    audio_kbps = 96 if total_kbps >= 600 else 64 if total_kbps >= 160 else 48
    video_kbps = max(int(total_kbps - audio_kbps), 20)
    # Models such as Gemini look at about one frame per second, so with little room fewer frames
    # per second and a sharper picture serve them better than smooth motion.
    fps = None if video_kbps >= 900 else 12 if video_kbps >= 400 else 5
    # About 0.08 bits per pixel keeps a 16:9 picture recognizable.
    pixels = video_kbps * 1000 / (fps or 25) / 0.08
    height = next((height for height in (480, 360, 240) if height * height * 16 / 9 <= pixels), 144)
    return ShrinkPlan(video_kbps, audio_kbps, height, fps)


def ffmpeg_arguments(source: Path, target: Path, plan: ShrinkPlan) -> list[str]:
    filters = [f"scale=-2:'min(ih,{plan.max_height})'"]  # never enlarge
    if plan.fps:
        filters.append(f"fps={plan.fps}")
    return ["-y", "-i", str(source), "-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{plan.video_kbps}k", "-maxrate", f"{int(plan.video_kbps * 1.2)}k",
            "-bufsize", f"{plan.video_kbps * 2}k", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", f"{plan.audio_kbps}k", "-ac", "2", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", str(target)]


def shrink_video(source: Path, target: Path, target_base64: int,
                 progress: Callable[[float], None] = lambda _fraction: None,
                 cancelled: Callable[[], bool] = lambda: False) -> None:
    """Writes a copy of source to target that is at most target_base64 bytes once Base64-encoded.
    progress gets the done fraction of each attempt; raises ShrinkCancelled if cancelled() turns true."""
    from .tools import _ffmpeg, video_size_and_duration

    _size, duration = video_size_and_duration(source)
    goal = target_base64
    for _attempt in range(3):
        _run(_ffmpeg(), ffmpeg_arguments(source, target, shrink_plan(goal, duration)), duration, progress, cancelled)
        sent = base64_size(target.stat().st_size)
        if sent <= target_base64:
            return
        # The encoder overshot; aim lower by the same ratio, with some extra room.
        goal = int(goal * target_base64 / sent * 0.9)
    raise RuntimeError(f"ffmpeg could not shrink the video to {megabytes(target_base64)}; "
                       f"the smallest attempt was {megabytes(sent)}.")


def _run(ffmpeg: str, arguments: list[str], duration: float, progress: Callable[[float], None],
         cancelled: Callable[[], bool]) -> None:
    # The windowed app has no console; without this flag ffmpeg would flash a console window.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", *arguments], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, creationflags=flags)
    try:
        # ffmpeg reports its position about twice a second, so a cancel is noticed quickly.
        for line in process.stdout:
            if cancelled():
                raise ShrinkCancelled()
            if line.startswith("out_time_us=") and line[12:].strip().isdigit() and duration:
                progress(min(int(line[12:]) / 1_000_000 / duration, 1.0))
        errors = process.stderr.read()
        if process.wait() != 0:
            raise RuntimeError(errors.strip() or "ffmpeg could not shrink the video.")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
