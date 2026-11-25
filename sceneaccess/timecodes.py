"""Times in a video as people read them: mm:ss or h:mm:ss, and back from what a model or a person writes."""

from __future__ import annotations

from typing import Any


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
