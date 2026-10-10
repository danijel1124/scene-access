from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_primary_readme_is_english_and_links_to_german_translation():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "# SceneAccess" in readme
    assert "German translation" in readme
    assert (ROOT / "README.de.md").is_file()
