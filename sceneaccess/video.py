"""A chat's video on this computer, and ffmpeg on it: the video as a file, its size and duration, and one frame
cut out of it. ffmpeg runs without a console window and can be cancelled through a RequestControl."""

from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .request_control import RequestControl
from .session_store import VideoSession


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(arguments: list[str], timeout: float = 120,
               control: RequestControl | None = None) -> subprocess.CompletedProcess:
    # The windowed app has no console; without this flag every call would flash a console window.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    command = [_ffmpeg(), "-hide_banner", "-loglevel", "error", *arguments]
    if control is None:
        return subprocess.run(command, capture_output=True, text=True, creationflags=flags, timeout=timeout)
    control.check_cancelled()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
    def interrupt() -> None:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + timeout
    try:
        control.register_interrupt(interrupt)
        while True:
            control.check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                control.check_cancelled()
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        control.register_interrupt(None)
        if process.poll() is None:
            process.kill()
        process.communicate()


@contextmanager
def local_video(session: VideoSession) -> Iterator[Path]:
    """The chat's video as a file: the original, or a temporary copy of the stored one if it was moved."""
    source = Path(session.source_path)
    if source.is_file():
        yield source
        return
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / ("video" + source.suffix)
        copy.write_bytes(base64.b64decode(session.video_data_url.split(",", 1)[1]))
        yield copy


def video_size_and_duration(path: Path) -> tuple[tuple[int, int], float]:
    import imageio_ffmpeg
    frames = imageio_ffmpeg.read_frames(str(path))
    try:
        meta = next(frames)
    finally:
        frames.close()
    return tuple(meta["size"]), float(meta.get("duration") or 0)


def extract_frame(source: Path, seconds: float, region: tuple[float, float, float, float], target: Path,
                  control: RequestControl | None = None) -> None:
    """Writes one JPEG frame of source at seconds, cropped to region (fractions) and scaled for the model."""
    x, y, width, height = region
    filters = []
    if region != (0.0, 0.0, 1.0, 1.0):
        filters.append(f"crop=iw*{width}:ih*{height}:iw*{x}:ih*{y}")
        # Enlarge small regions: the model reads small images poorly. This adds no detail, only size.
        filters.append("scale=1024:1024:force_original_aspect_ratio=decrease:flags=lanczos")
    else:
        filters.append("scale='min(1536,iw)':'min(1536,ih)':force_original_aspect_ratio=decrease:flags=lanczos")
    completed = run_ffmpeg(["-ss", f"{seconds:.3f}", "-i", str(source), "-frames:v", "1", "-vf", ",".join(filters),
                            "-q:v", "2", "-y", str(target)], **({"control": control} if control else {}))
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(completed.stderr.strip() or "ffmpeg did not produce an image at this time.")
