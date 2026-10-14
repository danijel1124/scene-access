import wx

from sceneaccess import gui, new_chat


def test_new_video_chat_menu_runs_the_chosen_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    app = wx.App(False)
    frame = gui.MainFrame()
    calls = []
    monkeypatch.setattr(frame, "new_from_local_file", lambda: calls.append("local"))
    monkeypatch.setattr(frame, "new_from_youtube", lambda: calls.append("youtube"))
    try:
        for position, expected in ((0, "local"), (1, "youtube")):
            monkeypatch.setattr(frame.new_button, "GetPopupMenuSelectionFromUser",
                                lambda menu, _pos, p=position: menu.FindItemByPosition(p).GetId())
            frame.on_new(None)
            assert calls[-1] == expected
        monkeypatch.setattr(frame.new_button, "GetPopupMenuSelectionFromUser", lambda _menu, _pos: wx.ID_NONE)
        frame.on_new(None)
        assert calls == ["local", "youtube"]
    finally:
        frame.Destroy()
        app.Destroy()


class FakeCheckBox:
    def __init__(self, shown, value):
        self.shown, self.value = shown, value

    def IsShown(self):
        return self.shown

    def GetValue(self):
        return self.value


def fake_size_dialog(choice, calls, dont_ask=False):
    """A size dialog that returns choice, or the next of a list of choices each time it is shown."""
    choices = list(choice) if isinstance(choice, (list, tuple)) else None

    class FakeSizeDialog:
        def __init__(self, _parent, size, model, online, can_lower):
            calls.append((size, model, online, can_lower))
            self.dont_ask = FakeCheckBox(model != "openrouter/auto", dont_ask)

        def ShowModal(self):
            return choices.pop(0) if choices is not None else choice

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return FakeSizeDialog


def test_the_size_question_comes_only_for_too_large_videos_that_are_not_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    monkeypatch.setattr(new_chat, "SIZE_LIMIT", 100)
    small, large = tmp_path / "small.mp4", tmp_path / "large.mp4"
    small.write_bytes(b"x" * 60)  # 80 bytes once Base64-encoded
    large.write_bytes(b"x" * 300)
    calls = []
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        monkeypatch.setattr(new_chat, "SizeDialog", fake_size_dialog(new_chat.SIZE_SEND, calls, dont_ask=True))
        frame.config = gui.replace(frame.config, model="a/one")
        assert frame._video_to_send(small, online=False) == small and calls == []
        assert frame._video_to_send(large, online=True, lower_height=360) == large
        assert calls == [(300, "a/one", True, True)] and frame.pending_size_skip == "a/one"
        frame.config = gui.replace(frame.config, size_check_skipped_models=("a/one",))
        assert frame._video_to_send(large, online=False) == large and len(calls) == 1
        frame.config = gui.replace(frame.config, model="openrouter/auto", size_check_skipped_models=("openrouter/auto",))
        monkeypatch.setattr(new_chat, "SizeDialog", fake_size_dialog(new_chat.SIZE_BACK, calls))
        assert frame._video_to_send(large, online=False) == new_chat.SIZE_BACK  # openrouter/auto is never skipped
        frame.config = gui.replace(frame.config, check_video_size=False)
        assert frame._video_to_send(large, online=False) == large and len(calls) == 2
        shrunk = []
        monkeypatch.setattr(frame, "_shrink_with_progress", lambda source: shrunk.append(source) or tmp_path / "s.mp4")
        frame.config = gui.replace(frame.config, auto_shrink=True, shrink_target_mb=1)
        assert frame._video_to_send(large, online=False) == large  # below the user's own target
        monkeypatch.setattr(frame, "_shrink_target", lambda: 100)
        assert frame._video_to_send(large, online=False) == tmp_path / "s.mp4" and shrunk == [large]
        assert len(calls) == 2  # automatic shrinking never asks
    finally:
        frame.Destroy()
        app.Destroy()


def run_youtube_chat(tmp_path, monkeypatch, sizes, choices):
    """Starts a chat from YouTube with fake downloads of the given sizes per height; returns the
    downloaded heights, the size dialog calls, the started chats and how often the start dialog came."""
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    monkeypatch.setattr(new_chat, "SIZE_LIMIT", 100)
    monkeypatch.setattr(new_chat, "url_from_clipboard", lambda: "")
    downloads, started, calls, asked = [], [], [], []
    app = wx.App(False)
    frame = gui.MainFrame()

    def download(_url, target_dir, height):
        path = target_dir / "Video [abcdefghijk].mp4"
        assert not path.exists()  # the larger file was deleted first
        path.write_bytes(b"x" * sizes[height])
        downloads.append(height)
        return path, "Video", {}

    class FakeEntry:
        def __init__(self, *args):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def GetValue(self):
            return "https://www.youtube.com/watch?v=abcdefghijk"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    try:
        monkeypatch.setattr(gui.wx, "TextEntryDialog", FakeEntry)
        monkeypatch.setattr(frame, "_ask_first_question",
                            lambda online, previous=None: asked.append(previous) or ("Describe it.", ""))
        monkeypatch.setattr(frame, "_download_dir", lambda: tmp_path)
        monkeypatch.setattr(frame, "_download_with_progress", download)
        monkeypatch.setattr(new_chat, "video_height", lambda path: downloads[-1])
        monkeypatch.setattr(new_chat, "SizeDialog", fake_size_dialog(choices, calls))
        monkeypatch.setattr(frame, "_start_chat", lambda path, title, *start, **options: started.append((path, options)))
        frame.config = gui.replace(frame.config, youtube_max_height=480)
        frame.new_from_youtube()
        return downloads, calls, started, asked
    finally:
        frame.Destroy()
        app.Destroy()


def test_a_lower_resolution_is_requested_step_by_step_until_the_video_fits(tmp_path, monkeypatch):
    downloads, calls, started, _asked = run_youtube_chat(tmp_path, monkeypatch, {480: 300, 360: 200, 240: 60},
                                                         [new_chat.SIZE_LOWER])
    # Asked once; 360p was still too large, so 240p followed without asking again.
    assert downloads == [480, 360, 240] and len(calls) == 1
    assert started[0][1]["sent_video"] == tmp_path / "Video [abcdefghijk].mp4"


def test_going_back_keeps_a_video_already_stepped_down(tmp_path, monkeypatch):
    downloads, calls, started, asked = run_youtube_chat(
        tmp_path, monkeypatch, {480: 300, 360: 200, 240: 150}, [new_chat.SIZE_LOWER, new_chat.SIZE_BACK, new_chat.SIZE_SEND])
    # 240p is the lowest step and still too large: asked again with no lower step left, then back and send.
    assert downloads == [480, 360, 240] and len(asked) == 2
    assert [call[3] for call in calls] == [True, False, False]
    assert started and started[0][1]["sent_video"].stat().st_size == 150


def test_default_download_folder_is_created_inside_an_existing_videos_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    monkeypatch.setattr(new_chat, "default_download_dir", lambda: tmp_path / "Videos" / "scene-access")
    (tmp_path / "Videos").mkdir()
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        assert frame._download_dir() == tmp_path / "Videos" / "scene-access"
        assert (tmp_path / "Videos" / "scene-access").is_dir()
    finally:
        frame.Destroy()
        app.Destroy()
