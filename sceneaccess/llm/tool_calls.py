"""What a tool call looks like in a chat, apart from running it (tools.py): the tools' names, a tool's result, a
readable line for a call in the Chat history, and the message that shows a tool's image to the model."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..storage.session_store import SESSION_FILE_PREFIX
from ..timecodes import format_time, parse_time

ZOOM_FRAME = "zoom_frame"
HD_FRAME = "high_resolution_frame"
VIDEO_INFO = "video_information"
# For llama.cpp chats: a search this computer runs through OpenRouter; only the search terms leave it.
WEB_SEARCH = "web_search"


@dataclass(frozen=True)
class ToolResult:
    text: str
    # File name in the session folder of an image to show to the model, if any.
    image: str | None = None
    # One line for the chat history, e.g. "Image of 02:51, region x 0.4 y 0.6 w 0.2 h 0.1".
    caption: str = ""


def frame_region(arguments: dict[str, Any]) -> tuple[float, float, float, float]:
    """The region of a frame a zoom asks for, as fractions x, y, width, height, kept within the frame."""
    x, y = float(arguments.get("x", 0) or 0), float(arguments.get("y", 0) or 0)
    width, height = float(arguments.get("width", 1) or 1), float(arguments.get("height", 1) or 1)
    x, y = min(max(x, 0.0), 0.99), min(max(y, 0.0), 0.99)
    width, height = min(max(width, 0.01), 1 - x), min(max(height, 0.01), 1 - y)
    return x, y, width, height


def describe_call(name: str, arguments_json: str) -> str:
    """A readable line for a tool call, for the chat history and the screen reader."""
    try:
        arguments = json.loads(arguments_json or "{}")
        if name in (ZOOM_FRAME, HD_FRAME):
            region = frame_region(arguments)
            where = ("" if region == (0.0, 0.0, 1.0, 1.0)
                     else ", region x {:g}, y {:g}, width {:g}, height {:g}".format(*region))
            action = "Zoom into" if name == ZOOM_FRAME else "Load in high resolution"
            return f"{action} {format_time(parse_time(arguments.get('time', 0)))}{where}"
        if name == VIDEO_INFO:
            return "Read the video information"
        if name == "describe_section_again":
            # The merge's tool, see sections.REDO_TOOL.
            return f"Have section {arguments.get('part', '?')} described again: {arguments.get('problems', '')}"
        if name == WEB_SEARCH:
            return f"Search the web through OpenRouter for {arguments.get('query', '')}"
    except Exception:
        pass
    return f"{name} {arguments_json}"


def image_message(result: ToolResult) -> dict[str, Any]:
    """The user message that shows a tool's image; many providers accept images only from the user."""
    return {"role": "user", "content": [
        {"type": "text", "text": result.text},
        {"type": "image_url", "image_url": {"url": SESSION_FILE_PREFIX + (result.image or "")}},
    ]}
