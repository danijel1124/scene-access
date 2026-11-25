"""What the chat list and the chat windows share: the settings and OpenRouter's model catalogue.
gui.ChatWindows keeps one of each for all windows of the app."""

from __future__ import annotations

from typing import Any

from ..settings.config import AppConfig, resolve_openrouter_key
from ..llm.models import ModelCatalog


class AppWindow:
    """Mixed into the app's windows. windows is the shared gui.ChatWindows; None for a window on its own,
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
