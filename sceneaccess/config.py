from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    model: str = "google/gemini-2.5-flash"
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    api_key: str = ""


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
    )


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "model": config.model,
        "base_url": config.base_url,
        "api_key": config.api_key,
    }, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
