"""The tools a chat offers the model, and the code that runs the model's calls on this computer. What a call
looks like in the chat is tool_calls.py; ffmpeg and the chat's video file are video.py."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Collection

from ..providers import OPENROUTER
from ..request_control import RequestCancelled, RequestControl
from ..storage.session_store import VideoSession
from ..timecodes import format_time, parse_time
from ..llm.tool_calls import HD_FRAME, VIDEO_INFO, WEB_SEARCH, ZOOM_FRAME, ToolResult, frame_region
from ..media.video import extract_frame, local_video, video_size_and_duration
from ..worker_process import run_isolated_task

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


def tool_definitions(*, online_video: bool, web_search: bool = True, local_web_search: bool = False,
                     chosen: Collection[str] | None = None) -> list[dict[str, Any]]:
    """The tools of a new chat, in a fixed order so the request stays byte-identical for the cache.
    web_search is OpenRouter's own search; local_web_search the web_search tool for llama.cpp chats.
    chosen keeps only the tools with these keys (see tool_key); None keeps all."""
    tools = _all_tools(online_video, web_search, local_web_search)
    return tools if chosen is None else [tool for tool in tools if tool_key(tool) in chosen]


def tool_key(tool: dict[str, Any]) -> str:
    """A tool's name for choosing it; both kinds of web search are WEB_SEARCH."""
    return tool["function"]["name"] if tool.get("type") == "function" else WEB_SEARCH


def _all_tools(online_video: bool, web_search: bool, local_web_search: bool) -> list[dict[str, Any]]:
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
    if local_web_search:
        tools.append(_function(
            WEB_SEARCH,
            "Searches the web and returns the findings with their sources. Only the query leaves this computer, "
            "never the video. Use it when it helps to identify something you have found.",
            {"query": {"type": "string", "description": "What to search for."}}, ["query"],
        ))
    return tools


def function_tool_names(tools: list[dict[str, Any]]) -> list[str]:
    return [tool["function"]["name"] for tool in tools if tool.get("type") == "function"]


def tools_for_provider(tools: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """A chat's tools after it moved to another provider: the same choice, with that provider's web search."""
    keys = [tool_key(tool) for tool in tools]
    return tool_definitions(online_video=HD_FRAME in keys or VIDEO_INFO in keys, web_search=provider == OPENROUTER,
                            local_web_search=provider != OPENROUTER, chosen=keys)


def provider_tools(tools: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """The tools a provider can be sent: other servers reject OpenRouter's own tools, and a rejection
    would make the request drop all tools."""
    if provider == OPENROUTER:
        return tools
    return [tool for tool in tools if not str(tool.get("type", "")).startswith("openrouter:")]


class ToolRunner:
    """Runs the model's tool calls for one chat. Errors become text, so the model can react to them."""

    def __init__(self, session: VideoSession, youtube_cookies: str = "", control: RequestControl | None = None,
                 web_search: Callable[[str], ToolResult] | None = None):
        self.session = session
        self.youtube_cookies = youtube_cookies
        self.control = control
        # Runs the web_search tool; None if no OpenRouter key is available to run it with.
        self.web_search = web_search
        self._created_images: set[Path] = set()

    def commit_files(self) -> None:
        """The turn's history now owns its generated images."""
        self._created_images.clear()

    def cleanup_files(self) -> None:
        """Discard only this unfinished turn's images, including partial extractions."""
        for path in tuple(self._created_images):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            self._created_images.discard(path)

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
            if name == WEB_SEARCH:
                return self._web_search(arguments)
            raise ValueError(f"The tool {name} is not available.")
        except RequestCancelled:
            raise
        except Exception as error:
            return ToolResult(f"Error: {error}", caption=f"{name} failed: {error}")

    def _zoom_frame(self, arguments: dict[str, Any]) -> ToolResult:
        seconds, region = parse_time(arguments.get("time", 0)), frame_region(arguments)
        with local_video(self.session) as source:
            return self._frame_from(source, seconds, region, "the chat video")

    def _high_resolution_frame(self, arguments: dict[str, Any]) -> ToolResult:
        from ..media.youtube import download_section

        if not self.session.source_url:
            raise ValueError("This chat has no online source.")
        seconds, region = parse_time(arguments.get("time", 0)), frame_region(arguments)
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
        self._created_images.add(self.session.directory / name)
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

    def _web_search(self, arguments: dict[str, Any]) -> ToolResult:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("The search needs a query.")
        if self.web_search is None:
            raise ValueError("The web search needs an OpenRouter API key, and none is configured.")
        return self.web_search(query)


