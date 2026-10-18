"""Tools the model can call during a chat, and the code that runs them on this computer."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import uuid
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .session_store import SESSION_FILE_PREFIX, VideoSession
from .request_control import RequestControl, RequestCancelled
from .worker_process import run_isolated_task

ZOOM_FRAME = "zoom_frame"
HD_FRAME = "high_resolution_frame"
VIDEO_INFO = "video_information"

_REGION_PROPERTIES = {
    "x": {"type": "number", "description": "Left edge of the region, 0 to 1 of the frame width. Default 0."},
    "y": {"type": "number", "description": "Top edge of the region, 0 to 1 of the frame height. Default 0."},
    "width": {"type": "number", "description": "Width of the region, 0 to 1 of the frame width. Default 1."},
    "height": {"type": "number", "description": "Height of the region, 0 to 1 of the frame height. Default 1."},
}
_TIME_PROPERTY = {"type": "string", "description": "Time in the video as mm:ss, h:mm:ss or seconds, e.g. \"02:51.5\"."}


def _function(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required}}}


def tool_definitions(*, online_video: bool, web_search: bool = True) -> list[dict[str, Any]]:
    """The tools of a new chat, in a fixed order so the request stays byte-identical for the cache."""
    tools = [_function(
        ZOOM_FRAME,
        "Returns a still image of one moment of the video, optionally only a region of it, enlarged. Use it to "
        "read small text, logos, labels or other details. The image follows the tool result as a user message.",
        {"time": _TIME_PROPERTY, **_REGION_PROPERTIES}, ["time"],
    )]
    if online_video:
        tools.append(_function(
            HD_FRAME,
            "Like zoom_frame, but loads that moment from the online source in the highest available resolution "
            "(up to 2160p) first. Slower; use it when zoom_frame is too blurry, because the chat video is downscaled.",
            {"time": _TIME_PROPERTY, **_REGION_PROPERTIES}, ["time"],
        ))
        tools.append(_function(
            VIDEO_INFO,
            "Returns the online video's title, channel, upload date, description, tags and chapters. The "
            "description often names products, music and people shown in the video.",
            {}, [],
        ))
    if web_search:
        # Run by OpenRouter within the same request; the model decides whether and how often to search.
        tools.append({"type": "openrouter:web_search", "parameters": {"max_results": 5}})
    return tools


def function_tool_names(tools: list[dict[str, Any]]) -> list[str]:
    return [tool["function"]["name"] for tool in tools if tool.get("type") == "function"]


@dataclass(frozen=True)
class ToolResult:
    text: str
    # File name in the session folder of an image to show to the model, if any.
    image: str | None = None
    # One line for the chat history, e.g. "Image of 02:51, region x 0.4 y 0.6 w 0.2 h 0.1".
    caption: str = ""


def parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        parts = str(value).strip().split(":")
        if not 1 <= len(parts) <= 3:
            raise ValueError(f"Unreadable time {value!r}; use mm:ss, h:mm:ss or seconds.")
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
    if seconds < 0:
        raise ValueError("The time must not be negative.")
    return seconds


def format_time(seconds: float) -> str:
    minutes, rest = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    text = f"{minutes:02d}:{rest:04.1f}" if rest % 1 else f"{minutes:02d}:{int(rest):02d}"
    return f"{hours}:{text}" if hours else text


def _region(arguments: dict[str, Any]) -> tuple[float, float, float, float]:
    x, y = float(arguments.get("x", 0) or 0), float(arguments.get("y", 0) or 0)
    width, height = float(arguments.get("width", 1) or 1), float(arguments.get("height", 1) or 1)
    x, y = min(max(x, 0.0), 0.99), min(max(y, 0.0), 0.99)
    width, height = min(max(width, 0.01), 1 - x), min(max(height, 0.01), 1 - y)
    return x, y, width, height


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


class ToolRunner:
    """Runs the model's tool calls for one chat. Errors become text, so the model can react to them."""

    def __init__(self, session: VideoSession, youtube_cookies: str = "", control: RequestControl | None = None):
        self.session = session
        self.youtube_cookies = youtube_cookies
        self.control = control

    def run(self, name: str, arguments_json: str) -> ToolResult:
        try:
            if self.control:
                self.control.check_cancelled()
            arguments = json.loads(arguments_json or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("The arguments must be a JSON object.")
            if name not in function_tool_names(self.session.tools):
                raise ValueError(f"There is no tool named {name} in this chat.")
            if name == ZOOM_FRAME:
                return self._zoom_frame(arguments)
            if name == HD_FRAME:
                return self._high_resolution_frame(arguments)
            if name == VIDEO_INFO:
                return self._video_information()
            raise ValueError(f"The tool {name} is not available.")
        except RequestCancelled:
            raise
        except Exception as error:
            return ToolResult(f"Error: {error}", caption=f"{name} failed: {error}")

    def _zoom_frame(self, arguments: dict[str, Any]) -> ToolResult:
        seconds, region = parse_time(arguments.get("time", 0)), _region(arguments)
        with local_video(self.session) as source:
            return self._frame_from(source, seconds, region, "the chat video")

    def _high_resolution_frame(self, arguments: dict[str, Any]) -> ToolResult:
        from .youtube import download_section

        if not self.session.source_url:
            raise ValueError("This chat has no online source.")
        seconds, region = parse_time(arguments.get("time", 0)), _region(arguments)
        with tempfile.TemporaryDirectory() as folder:
            # One second is enough for a still frame; the clip starts exactly at the requested time.
            if self.control:
                clip = run_isolated_task(download_section,
                                      (self.session.source_url, seconds, seconds + 1, Path(folder)),
                                      kwargs={"cookies": self.youtube_cookies}, control=self.control)
                self.control.check_cancelled()
            else:
                clip = download_section(self.session.source_url, seconds, seconds + 1, Path(folder),
                                        cookies=self.youtube_cookies)
            result = self._frame_from(clip, 0.0, region, "the online source", reported_seconds=seconds)
        return result

    def _frame_from(self, source: Path, seconds: float, region: tuple[float, float, float, float], origin: str,
                    reported_seconds: float | None = None) -> ToolResult:
        shown = seconds if reported_seconds is None else reported_seconds
        if self.control:
            self.control.check_cancelled()
            (width, height), duration = run_isolated_task(video_size_and_duration, (source,),
                                                      control=self.control, timeout=30)
            self.control.check_cancelled()
        else:
            (width, height), duration = video_size_and_duration(source)
        if reported_seconds is None and duration and seconds > duration:
            raise ValueError(f"{format_time(seconds)} is after the end of the video ({format_time(duration)}).")
        name = f"frame_{uuid.uuid4().hex[:12]}.jpg"
        extract_frame(source, seconds, region, self.session.directory / name,
                      **({"control": self.control} if self.control else {}))
        x, y, w, h = region
        region_text = "the whole frame" if region == (0.0, 0.0, 1.0, 1.0) else f"region x {x:g}, y {y:g}, width {w:g}, height {h:g}"
        pixels = f"{round(width * w)}x{round(height * h)} source pixels of a {width}x{height} frame"
        text = f"Image of {format_time(shown)} from {origin}, {region_text} ({pixels}). It follows as the next message."
        return ToolResult(text, image=name, caption=f"Image of {format_time(shown)}, {region_text}, {pixels}")

    def _video_information(self) -> ToolResult:
        if not self.session.video_info:
            raise ValueError("No video information was stored for this chat.")
        return ToolResult(json.dumps(self.session.video_info, ensure_ascii=False, indent=1),
                          caption="Video information read")


def describe_call(name: str, arguments_json: str) -> str:
    """A readable line for a tool call, for the chat history and the screen reader."""
    try:
        arguments = json.loads(arguments_json or "{}")
        if name in (ZOOM_FRAME, HD_FRAME):
            region = _region(arguments)
            where = "" if region == (0.0, 0.0, 1.0, 1.0) else ", region x {:g}, y {:g}, width {:g}, height {:g}".format(*region)
            action = "Zoom into" if name == ZOOM_FRAME else "Load in high resolution"
            return f"{action} {format_time(parse_time(arguments.get('time', 0)))}{where}"
        if name == VIDEO_INFO:
            return "Read the video information"
    except Exception:
        pass
    return f"{name} {arguments_json}"


def image_message(result: ToolResult) -> dict[str, Any]:
    """The user message that shows a tool's image; many providers accept images only from the user."""
    return {"role": "user", "content": [
        {"type": "text", "text": result.text},
        {"type": "image_url", "image_url": {"url": SESSION_FILE_PREFIX + (result.image or "")}},
    ]}
