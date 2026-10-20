from sceneaccess.config import AppConfig
from sceneaccess.session_store import VideoSessionStore


def test_sceneaccess_uses_its_own_persistent_database_name(tmp_path):
    assert VideoSessionStore(tmp_path).db_path == tmp_path / "sceneaccess.db"


def test_new_installations_default_to_gemini_flash():
    assert AppConfig().model == "google/gemini-2.5-flash"
