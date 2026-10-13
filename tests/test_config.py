from pathlib import Path

from sceneaccess.config import AppConfig, config_path_from_standard_paths, load_config, save_config


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
