import json

import wx

from sceneaccess import gui
from sceneaccess.prompts import BASE_SYSTEM_PROMPT


def test_enter_sends_the_question_and_shift_enter_keeps_the_line_break(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    app = wx.App(False)
    frame = gui.MainFrame()
    sent, opened = [], []
    monkeypatch.setattr(frame, "on_send", lambda _event: sent.append(True))
    monkeypatch.setattr(frame, "open_selected_message", lambda: opened.append(True))
    try:
        assert frame.handle_enter(frame.question, wx.WXK_RETURN, shift=True) is False
        assert sent == []
        assert frame.handle_enter(frame.question, wx.WXK_RETURN, shift=False) is True
        assert frame.handle_enter(frame.question, wx.WXK_NUMPAD_ENTER, shift=False) is True
        assert sent == [True, True]
        assert frame.handle_enter(frame.chat_list,wx.WXK_RETURN, shift=False) is True
        assert opened == [True]
        assert frame.handle_enter(frame.sessions, wx.WXK_RETURN, shift=False) is False
        assert frame.handle_enter(frame.question, ord("A"), shift=False) is False
    finally:
        frame.Destroy()
        app.Destroy()


def test_dont_ask_again_is_kept_only_once_the_answer_arrived(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    monkeypatch.setattr(gui, "announce", lambda *args, **kwargs: None)
    monkeypatch.setattr(gui.wx, "MessageBox", lambda *args, **kwargs: wx.OK)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=video, title="V", model="a/one",
                                                   initial_question="Describe it.")
        frame.size_skip_after_answer = (frame.current.id, "a/one")
        frame._failed("HTTP 400: Invalid content")
        assert frame.config.size_check_skipped_models == ()
        frame.size_skip_after_answer = (frame.current.id, "a/one")
        frame._completed(frame.current.id, "a/one")
        assert gui.load_config(tmp_path / "config.json").size_check_skipped_models == ("a/one",)
    finally:
        frame.Destroy()
        app.Destroy()


def test_a_chat_from_before_tools_gets_the_prompt_and_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    video = tmp_path / "old.mp4"
    video.write_bytes(b"fake-mp4-payload")
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=video, title="Old", model="m",
                                                   initial_question="Describe it.")
        assert frame.current.tools == [] and frame.current.system_prompt == ""
        frame.apply_setup("Identify brand and model")
        session = frame.store.get_session(frame.current.id, gui.OWNER_ID)
        assert "zoom_frame" in json.dumps(session.tools) and "high_resolution_frame" not in json.dumps(session.tools)
        assert session.system_prompt.startswith(BASE_SYSTEM_PROMPT)
        assert session.system_prompt.endswith(frame.config.profile_text("Identify brand and model"))
    finally:
        frame.Destroy()
        app.Destroy()


def test_a_failed_transcription_is_remembered_and_does_not_stop_the_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    monkeypatch.setattr(gui, "needs_transcript", lambda _session, _catalog: True)

    def fail(_session, _client):
        raise RuntimeError("HTTP 502: upstream audio operation failed")

    monkeypatch.setattr(gui, "make_transcript", fail)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        session = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=video, title="T", model="m",
                                             initial_question="?", transcript_state="pending")
        frame._prepare_transcript(session, client=None)
        assert frame.store.get_session(session.id, gui.OWNER_ID).transcript_state == "failed"
    finally:
        frame.Destroy()
        app.Destroy()


def test_older_youtube_chats_are_recognized_by_the_id_in_the_file_name():
    assert gui.youtube_link_in_file_name(r"C:\Videos\scene-access\Me at the zoo [jNQXAC9IVRw].mp4") == \
        "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    assert gui.youtube_link_in_file_name(r"C:\Videos\holiday.mp4") is None
    assert gui.is_local_file_chat("holiday.mp4") is True
    # A download is titled with the video title, so it is searched for even without an ID in the file name.
    assert gui.is_local_file_chat("Me at the zoo") is False
