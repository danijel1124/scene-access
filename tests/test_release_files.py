from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_release_build_has_a_launcher_and_actions_workflow():
    assert (ROOT / "SceneAccess.py").is_file()
    assert (ROOT / ".github" / "workflows" / "windows-release.yml").is_file()
    assert (ROOT / "requirements-dev.txt").is_file()
