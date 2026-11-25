"""What the chat list and the chat windows share: where the settings lie, the settings and OpenRouter's model
catalogue. app.ChatWindows keeps one of each for all windows of the app."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import wx

from ..settings.config import AppConfig, config_path_from_standard_paths, resolve_openrouter_key
from ..llm.models import ModelCatalog


def config_path() -> Path:
    """Where the app keeps config.json, beside its chats: wx's user data folder. Every window asks here, so a test
    that patches config_path_from_standard_paths in this module points them all at its own folder."""
    return config_path_from_standard_paths(wx.StandardPaths.Get())


class AppWindow:
    """Mixed into the app's windows. windows is the shared app.ChatWindows; None for a window on its own,
    which then keeps its own settings and catalogue."""

    windows: Any = None

    @property
    def config(self) -> AppConfig:
        return self.windows.config if self.windows and self.windows.config is not None else self._config

    @config.setter
    def config(self, value: AppConfig) -> None:
        self._config = value
        if self.windows:
            self.windows.config = value

    @property
    def catalog(self) -> ModelCatalog:
        return self.windows.catalog if self.windows and self.windows.catalog is not None else self._catalog

    @catalog.setter
    def catalog(self, value: ModelCatalog) -> None:
        self._catalog = value
        if self.windows:
            self.windows.catalog = value

    def openrouter_key(self) -> str | None:
        return resolve_openrouter_key(self.config)
