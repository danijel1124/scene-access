"""What the chat list and the chat windows share: the settings, OpenRouter's model catalogue and the stored
chats. gui.ChatWindows keeps one of each for all windows of the app."""

from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import ModelCatalog
from .session_store import VideoSessionStore


def _local_user() -> str:
    # getpass also reads USERNAME, so it works on Windows, where USER is usually not set.
    try:
        return getpass.getuser() or "user"
    except Exception:
        return "user"


OWNER_ID = "local:" + _local_user()
# Earlier versions read only USER, so on Windows every chat was stored under this owner.
LEGACY_OWNER_ID = "local:" + os.environ.get("USER", "user")


def resolve_openrouter_key(config: AppConfig) -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or config.api_key or None


def open_store(config_path: Path, owner_id: str, legacy_owner_id: str) -> VideoSessionStore:
    store = VideoSessionStore(config_path.parent / "chats")
    if legacy_owner_id != owner_id:
        # Earlier versions stored every chat on Windows under one owner; see LEGACY_OWNER_ID.
        store.reassign_owner(legacy_owner_id, owner_id)
    return store


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
