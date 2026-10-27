import sys
import types
from pathlib import Path

from sceneaccess import youtube


def test_download_options_cap_resolution_and_merge_video_with_audio(tmp_path: Path):
    options = youtube.download_options(tmp_path, 360)

    assert options["format_sort"][0] == "res:360"
    assert "+size" in options["format_sort"]
    assert options["merge_output_format"] == "mp4"
    assert options["noplaylist"] is True
    assert options["paths"]["home"] == str(tmp_path)
    assert "node" in options["js_runtimes"]


def test_default_file_name_matches_the_yt_dlp_default():
    assert youtube.output_template(youtube.DEFAULT_FILENAME_FIELDS) == "%(title)s [%(id)s].%(ext)s"


def test_further_file_name_parts_follow_in_brackets_in_list_order():
    assert youtube.output_template(["upload_date", "id", "title"]) == "%(title)s [%(id)s] [%(upload_date)s].%(ext)s"
    assert youtube.output_template([]) == "%(title)s [%(id)s].%(ext)s"


def test_download_options_use_the_chosen_file_name_parts(tmp_path: Path):
    options = youtube.download_options(tmp_path, 480, filename_fields=["title", "uploader"])

    assert options["outtmpl"] == "%(title)s [%(uploader)s].%(ext)s"
    assert options["paths"]["home"] == str(tmp_path)
    assert options["trim_file_name"] > 0


def test_youtube_cookies_come_from_firefox_or_a_cookies_file(tmp_path: Path):
    assert youtube.cookie_options("") == {}
    assert youtube.cookie_options("firefox") == {"cookiesfrombrowser": ("firefox",)}
    assert youtube.cookie_options(r"C:\cookies.txt") == {"cookiefile": r"C:\cookies.txt"}
    assert youtube.download_options(tmp_path, 480, cookies="firefox")["cookiesfrombrowser"] == ("firefox",)
    assert "cookiesfrombrowser" not in youtube.download_options(tmp_path, 480)


def test_youtubes_bot_check_is_recognized():
    error = RuntimeError("ERROR: [youtube] jNQXAC9IVRw: Sign in to confirm you're not a bot. Use --cookies-from-browser")
    assert youtube.needs_sign_in(error) is True
    assert youtube.needs_sign_in(RuntimeError("HTTP Error 404: Not Found")) is False


def test_search_lists_videos_without_downloading(monkeypatch):
    seen = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            seen["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, query, download):
            seen["query"], seen["download"] = query, download
            return {"entries": [{"id": "jNQXAC9IVRw", "title": "Me at the zoo", "channel": "jawed", "duration": 19},
                                {"id": "x" * 11, "title": "Other", "uploader": "someone"}, None]}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    results = youtube.search_videos("Me at the zoo")

    assert seen["query"] == "ytsearch8:Me at the zoo" and seen["download"] is False
    assert seen["options"]["skip_download"] and seen["options"]["ignore_no_formats_error"]
    assert results == [
        {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "title": "Me at the zoo", "channel": "jawed", "duration": 19},
        {"url": "https://www.youtube.com/watch?v=" + "x" * 11, "title": "Other", "channel": "someone", "duration": None},
    ]


def test_download_video_returns_the_downloaded_file_and_title(tmp_path: Path, monkeypatch):
    seen = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            seen["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            seen["url"], seen["download"] = url, download
            return {"title": "Me at the zoo", "description": "The first video.", "tags": [], "formats": [{"url": "x"}],
                    "chapters": [{"start_time": 0, "end_time": 5, "title": "Intro", "extra": 1}],
                    "requested_downloads": [{"filepath": str(tmp_path / "downloads" / "jNQXAC9IVRw-240p.mp4")}]}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    path, title, info = youtube.download_video("https://www.youtube.com/watch?v=jNQXAC9IVRw", tmp_path / "downloads", 480)

    assert path == tmp_path / "downloads" / "jNQXAC9IVRw-240p.mp4"
    assert title == "Me at the zoo"
    # Only what the model may read: no format details, no empty fields, chapters trimmed.
    assert info == {"title": "Me at the zoo", "description": "The first video.",
                    "chapters": [{"start_time": 0, "end_time": 5, "title": "Intro"}]}
    assert seen["download"] is True
    assert seen["options"]["format_sort"][0] == "res:480"
    assert (tmp_path / "downloads").is_dir()
