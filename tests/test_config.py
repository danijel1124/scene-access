import json
from pathlib import Path

import pytest

from sceneaccess.config import AppConfig, config_path_from_standard_paths, load_config, save_config

@pytest.mark.parametrize("limit", [0, 6, 100000000000000000000])
def test_tool_round_limit_round_trips_without_an_upper_bound(tmp_path, limit):
    path = tmp_path / "config.json"
    save_config(path, AppConfig(max_tool_rounds=limit))
    assert load_config(path).max_tool_rounds == limit


@pytest.mark.parametrize("value", [-1, True, False, 1.5, "3", "bad", None, [], {}])
def test_invalid_tool_round_limits_fall_back_safely(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"max_tool_rounds": value}), encoding="utf-8")
    assert load_config(path).max_tool_rounds == 6


def test_legacy_config_gets_default_tool_round_limit(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    assert load_config(path).max_tool_rounds == AppConfig().max_tool_rounds == 6

class FakeStandardPaths:
    def __init__(self, root: Path):
        self.root = root

    def GetUserDataDir(self) -> str:
        return str(self.root)


def test_config_path_uses_wx_user_data_directory_not_a_posix_home_path(tmp_path: Path):
    path = config_path_from_standard_paths(FakeStandardPaths(tmp_path / "Windows-like-AppData"))

    assert path == tmp_path / "Windows-like-AppData" / "config.json"


def test_configuration_round_trip_keeps_model_endpoint_and_api_key(tmp_path: Path):
    path = tmp_path / "config.json"
    expected = AppConfig(
        model="openrouter/auto",
        base_url="https://router.example/api/v1/chat/completions",
        api_key="secret-for-test-only",
    )

    save_config(path, expected)

    assert load_config(path) == expected


def test_youtube_quality_round_trips_and_defaults_for_older_configs(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(path, AppConfig(youtube_max_height=720))
    assert load_config(path).youtube_max_height == 720

    path.write_text('{"model": "openrouter/auto", "base_url": "https://openrouter.ai/api/v1/chat/completions", "api_key": ""}', encoding="utf-8")
    assert load_config(path).youtube_max_height == 480

    path.write_text('{"youtube_max_height": "nonsense"}', encoding="utf-8")
    assert load_config(path).youtube_max_height == 480


def test_file_name_parts_round_trip_and_drop_unknown_fields(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(path, AppConfig(download_dir=str(tmp_path), filename_fields=("title", "uploader")))
    loaded = load_config(path)
    assert loaded.filename_fields == ("title", "uploader")
    assert loaded.download_dir == str(tmp_path)

    path.write_text('{"filename_fields": ["not_a_field"]}', encoding="utf-8")
    assert load_config(path).filename_fields == ("title", "id")


def test_prompt_profiles_round_trip_and_older_configs_get_the_defaults(tmp_path: Path):
    from sceneaccess.prompts import DEFAULT_PROFILES

    path = tmp_path / "config.json"
    save_config(path, AppConfig(profiles=(("Mine", "My text"),), profile="Mine"))
    loaded = load_config(path)
    assert loaded.profiles == (("Mine", "My text"),)
    assert loaded.profile_text("Mine") == "My text"

    path.write_text('{"model": "m"}', encoding="utf-8")
    assert load_config(path).profiles == DEFAULT_PROFILES

    # Deleting every profile is kept; duplicate and nameless entries are dropped.
    path.write_text('{"profiles": [{"name": "A", "text": "1"}, {"name": "A", "text": "2"}, {"name": " "}]}', encoding="utf-8")
    assert load_config(path).profiles == (("A", "1"),)
    path.write_text('{"profiles": []}', encoding="utf-8")
    assert load_config(path).profiles == ()


def test_sound_and_youtube_sign_in_settings_round_trip(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(path, AppConfig(audio_only_auto=True, youtube_cookies="firefox"))
    loaded = load_config(path)
    assert loaded.audio_only_auto is True and loaded.youtube_cookies == "firefox"

    path.write_text('{"model": "m"}', encoding="utf-8")
    assert load_config(path).audio_only_auto is False and load_config(path).youtube_cookies == ""


def test_excluded_providers_and_models_round_trip_without_blanks_or_duplicates(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(path, AppConfig(ignored_providers=("google-ai-studio",), excluded_models=("google/*", "z-ai/glm-flash")))
    loaded = load_config(path)
    assert loaded.ignored_providers == ("google-ai-studio",)
    assert loaded.excluded_models == ("google/*", "z-ai/glm-flash")

    path.write_text('{"ignored_providers": [" novita ", "", "novita"], "excluded_models": "google/*"}', encoding="utf-8")
    assert load_config(path).ignored_providers == ("novita",) and load_config(path).excluded_models == ()


def test_video_size_settings_round_trip_and_default_to_asking(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(path, AppConfig(check_video_size=False, size_check_skipped_models=("a/one",), auto_shrink=True,
                                shrink_target_mb=40))
    loaded = load_config(path)
    assert loaded.check_video_size is False and loaded.size_check_skipped_models == ("a/one",)
    assert loaded.auto_shrink is True and loaded.shrink_target_mb == 40

    path.write_text('{"model": "m", "shrink_target_mb": 0}', encoding="utf-8")
    loaded = load_config(path)
    assert loaded.check_video_size is True and loaded.auto_shrink is False and loaded.shrink_target_mb == 16
