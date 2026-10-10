from sceneaccess.config import AppConfig


def test_sceneaccess_uses_its_own_persistent_database_name():
    assert AppConfig().model == "google/gemini-2.5-flash"
