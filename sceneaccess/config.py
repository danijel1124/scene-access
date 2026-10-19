from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .prompts import DEFAULT_PROFILE, DEFAULT_PROFILES
from .video_size import DEFAULT_SHRINK_TARGET_MB
from .youtube import DEFAULT_FILENAME_FIELDS, FILENAME_FIELD_NAMES


class InvalidConfigError(ValueError):
    """The existing configuration cannot be safely loaded; leave it untouched."""


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
    # Where YouTube cookies come from: "" for none, a browser such as "firefox", or a cookies.txt path.
    youtube_cookies: str = ""
    # Provider slugs such as "google-ai-studio" that never answer, whatever the model.
    ignored_providers: tuple[str, ...] = ()
    # Model IDs or patterns such as "google/*" that openrouter/auto never picks.
    excluded_models: tuple[str, ...] = ()
    # Ask before a chat starts with a video too large for some providers.
    check_video_size: bool = True
    # Models for which the user chose not to be asked again, after sending a large video worked.
    size_check_skipped_models: tuple[str, ...] = ()
    # Shrink too large videos with ffmpeg without asking, to at most this many MB once Base64-encoded.
    auto_shrink: bool = False
    shrink_target_mb: int = DEFAULT_SHRINK_TARGET_MB
    # Maximum tool-call rounds per answer; zero disables tools, not an unlimited budget.
    max_tool_rounds: int = 6

    def profile_text(self, name: str) -> str:
        return dict(self.profiles).get(name, "")


def config_path_from_standard_paths(standard_paths: object) -> Path:
    return Path(standard_paths.GetUserDataDir()) / "config.json"


def load_config(path: Path) -> AppConfig:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppConfig()
    except json.JSONDecodeError as error:
        raise InvalidConfigError(
            f"Invalid configuration in '{path}': invalid JSON syntax at line {error.lineno}, "
            f"column {error.colno}. Please correct the file before restarting."
        ) from error
    if not isinstance(values, dict):
        raise InvalidConfigError(
            f"Invalid configuration in '{path}': expected a JSON object at the root. "
            "Please correct the file before restarting."
        )
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
        youtube_cookies=str(values.get("youtube_cookies", "")),
        ignored_providers=_names(values.get("ignored_providers")),
        excluded_models=_names(values.get("excluded_models")),
        check_video_size=values.get("check_video_size") is not False,
        size_check_skipped_models=_names(values.get("size_check_skipped_models")),
        auto_shrink=values.get("auto_shrink") is True,
        shrink_target_mb=_positive_int(values.get("shrink_target_mb"), DEFAULT_SHRINK_TARGET_MB),
        max_tool_rounds=(values["max_tool_rounds"]
                         if type(values.get("max_tool_rounds")) is int and values["max_tool_rounds"] >= 0
                         else AppConfig.max_tool_rounds),
    )


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names = [str(name).strip() for name in value if str(name).strip()]
    return tuple(dict.fromkeys(names))


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
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = json.dumps({
        "model": config.model,
        "base_url": config.base_url,
        "api_key": config.api_key,
        "youtube_max_height": config.youtube_max_height,
        "download_dir": config.download_dir,
        "filename_fields": list(config.filename_fields),
        "profiles": [{"name": name, "text": text} for name, text in config.profiles],
        "profile": config.profile,
        "audio_only_auto": config.audio_only_auto,
        "youtube_cookies": config.youtube_cookies,
        "ignored_providers": list(config.ignored_providers),
        "excluded_models": list(config.excluded_models),
        "check_video_size": config.check_video_size,
        "size_check_skipped_models": list(config.size_check_skipped_models),
        "auto_shrink": config.auto_shrink,
        "shrink_target_mb": config.shrink_target_mb,
        "max_tool_rounds": config.max_tool_rounds,
    }, indent=2)
    # mkstemp creates with 0600 before any secret is written, including in legacy
    # application-data directories that still have permissive directory modes.
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    os.chmod(path, 0o600)
