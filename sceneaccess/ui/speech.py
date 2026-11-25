"""Announcements through the running screen reader (speech and, where supported, braille)."""

from __future__ import annotations

import sys
from typing import Any

_auto: Any = None
_loaded = False

# Only reader-specific bridges are eligible. A synthesizer being available is
# not evidence that a screen reader is running; unknown outputs stay silent.
_SCREEN_READER_OUTPUTS = frozenset({
    'NVDA', 'Jaws', 'WindowEyes', 'SystemAccess', 'Dolphin',
    'PCTalker', 'ZDSR', 'VoiceOver',
})


def _point_frozen_lookup_at_bundled_files() -> None:
    # In a frozen app, accessible_output2 looks for its screen reader client DLLs next to the
    # executable, but PyInstaller 6 places bundled files in the _internal contents directory.
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle_dir:
        from platform_utils import paths
        paths.embedded_data_path = lambda: bundle_dir


def _screen_reader() -> Any:
    global _auto, _loaded
    if not _loaded:
        _loaded = True
        try:
            _point_frozen_lookup_at_bundled_files()
            from accessible_output2.outputs.auto import Auto
            _auto = Auto()
        except Exception:
            _auto = None
    if _auto is None:
        return None
    for output in _auto.outputs:
        # Exclude standalone speech engines such as SAPI, ESpeak and
        # SpeechDispatcher even when their is_active() reports availability.
        if type(output).__name__ not in _SCREEN_READER_OUTPUTS:
            continue
        try:
            if output.is_active():
                return output
        except Exception:
            continue
    return None


def announce(text: str, *, interrupt: bool = True) -> bool:
    output = _screen_reader()
    if output is None:
        return False
    # Speak and braille separately: Output.output() raises when speak() returns None, which some
    # outputs do even after speaking successfully.
    try:
        output.speak(text, interrupt=interrupt)
    except Exception:
        return False
    try:
        output.braille(text)
    except Exception:
        pass
    return True
