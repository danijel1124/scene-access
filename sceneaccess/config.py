from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .prompts import DEFAULT_PROFILE, DEFAULT_PROFILES
from .youtube import DEFAULT_FILENAME_FIELDS, FILENAME_FIELD_NAMES


@dataclass(frozen=True)
class AppConfig:
    model: str = "google/gemini-2.5-flash"
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    api_key: str = ""
    # Highest video height downloaded from YouTube; enough to recognize details without HD sizes.
    youtube_max_height: int = 480
    # Folder for downloaded videos; empty means the default <Videos>/scene-access.
    download_dir: str = ""
    # yt-dlp fields that make up a downloaded file's name; title and ID match yt-dlp's default.
    filename_fields: tuple[str, ...] = DEFAULT_FILENAME_FIELDS
    # Prompt profiles as (name, text); a new chat stores the text of the one it starts with.
    profiles: tuple[tuple[str, str], ...] = DEFAULT_PROFILES
    # The profile chosen last, preselected for the next chat.
    profile: str = DEFAULT_PROFILE
    # With openrouter/auto, let new chats choose only from models that hear the video's sound.
    audio_only_auto: bool = False

    def profile_text(self, name: str) -> str:
        return dict(self.profiles).get(name, "")


def config_path_from_standard_paths(standard_paths: object) -> Path:
    return Path(standard_paths.GetUserDataDir()) / "config.json"


def load_config(path: Path) -> AppConfig:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()
    return AppConfig(
        model=str(values.get("model", AppConfig.model)),
        base_url=str(values.get("base_url", AppConfig.base_url)),
        api_key=str(values.get("api_key", "")),
        youtube_max_height=_positive_int(values.get("youtube_max_height"), AppConfig.youtube_max_height),
        download_dir=str(values.get("download_dir", "")),
        filename_fields=_filename_fields(values.get("filename_fields")),
        profiles=_profiles(values.get("profiles")),
        profile=str(values.get("profile", DEFAULT_PROFILE)),
        audio_only_auto=values.get("audio_only_auto") is True,
    )


def _profiles(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        return DEFAULT_PROFILES
    profiles, names = [], set()
    for entry in value:
        if isinstance(entry, dict) and str(entry.get("name", "")).strip() and str(entry["name"]).strip() not in names:
            names.add(str(entry["name"]).strip())
            profiles.append((str(entry["name"]).strip(), str(entry.get("text", ""))))
    # An empty list is a valid choice: chats then use only the base prompt.
    return tuple(profiles)


def _filename_fields(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return DEFAULT_FILENAME_FIELDS
    fields = tuple(name for name in FILENAME_FIELD_NAMES if name in value)
    return fields or DEFAULT_FILENAME_FIELDS


def _positive_int(value: object, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "model": config.model,
        "base_url": config.base_url,
        "api_key": config.api_key,
        "youtube_max_height": config.youtube_max_height,
        "download_dir": config.download_dir,
        "filename_fields": list(config.filename_fields),
        "profiles": [{"name": name, "text": text} for name, text in config.profiles],
        "profile": config.profile,
        "audio_only_auto": config.audio_only_auto,
    }, indent=2), encoding="utf-8")
    # Restricts access on POSIX. On Windows chmod only toggles the read-only flag; there the
    # per-user application-data directory's access control list keeps the file private.
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
