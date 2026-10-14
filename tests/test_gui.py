import json

import wx

from sceneaccess import gui
from sceneaccess.openrouter import Completion, Turn
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


def test_a_chat_without_its_own_model_follows_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        frame.config = gui.replace(frame.config, model="openrouter/auto")
        frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=video, title="V", model="",
                                                   initial_question="?")
        assert frame.chat_model(frame.current) == "openrouter/auto"
        frame.render_current()
        assert frame.status.GetLabel().startswith("V | openrouter/auto (from Settings)")
        frame.store.update_chat_settings(frame.current.id, gui.OWNER_ID, "z-ai/glm-flash", False)
        assert frame.chat_model(frame.store.get_session(frame.current.id, gui.OWNER_ID)) == "z-ai/glm-flash"
    finally:
        frame.Destroy()
        app.Destroy()


class InlineThread:
    """Runs a worker thread's target at once, so a request can be checked without waiting."""

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def test_a_default_chat_sends_the_settings_model_and_gets_a_transcript_if_it_cannot_hear(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    monkeypatch.setattr(gui, "announce", lambda *args, **kwargs: None)
    monkeypatch.setattr(gui.threading, "Thread", InlineThread)
    monkeypatch.setattr(gui.wx, "CallAfter", lambda function, *args, **kwargs: function(*args, **kwargs))
    monkeypatch.setattr(gui, "needs_transcript", lambda session, _catalog: session.model == "z-ai/glm-flash")
    monkeypatch.setattr(gui, "make_transcript", lambda _session, _client: "[00:01] Hello")
    payloads = []

    def fake_run_turn(_client, session, messages, run_tool, on_event=None, routing=None):
        payloads.append((session.model, routing, messages))
        return Turn(entries=[{"role": "assistant", "content": "Answer"}], completion=Completion(
            "Answer", model=session.model, prompt_tokens=1, cached_tokens=None, cache_write_tokens=None))

    monkeypatch.setattr(gui.OpenRouterClient, "run_turn", fake_run_turn)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        frame.config = gui.replace(frame.config, model="google/gemini-flash", api_key="test-key")
        frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=video, title="V", model="",
                                                   initial_question="?", transcript_state="none")
        frame._send("What happens?", initial=False)
        assert payloads[-1][0] == "google/gemini-flash" and payloads[-1][1] == {}
        assert frame.store.get_session(frame.current.id, gui.OWNER_ID).transcript_state == "none"
        # Settings now name a model that cannot hear: the default chat gets a transcript first.
        frame.config = gui.replace(frame.config, model="z-ai/glm-flash")
        frame._send("And now?", initial=False)
        assert payloads[-1][0] == "z-ai/glm-flash"
        assert "[00:01] Hello" in json.dumps(payloads[-1][2])
        stored = frame.store.get_session(frame.current.id, gui.OWNER_ID)
        assert stored.transcript_state == "done" and stored.model == ""
    finally:
        frame.Destroy()
        app.Destroy()


def test_changing_the_profile_keeps_the_chats_own_hearing_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        frame.config = gui.replace(frame.config, audio_only_auto=True)
        frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=video, title="V", model="",
                                                   initial_question="?", audio_only=True)
        frame.store.update_chat_settings(frame.current.id, gui.OWNER_ID, "", False)
        frame.current = frame.store.get_session(frame.current.id, gui.OWNER_ID)
        frame.apply_setup("Identify brand and model")
        assert frame.store.get_session(frame.current.id, gui.OWNER_ID).audio_only is False
    finally:
        frame.Destroy()
        app.Destroy()


def test_a_question_in_a_chat_with_a_too_large_video_asks_first(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "config_path_from_standard_paths", lambda _paths: tmp_path / "config.json")
    monkeypatch.setattr(gui, "SIZE_LIMIT", 50)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 300)
    shown, choices = [], []

    class FakeStoredSizeDialog:
        def __init__(self, _parent, size, model):
            shown.append((size, model))
            self.dont_ask = type("Box", (), {"IsShown": lambda self: True, "GetValue": lambda self: True})()

        def ShowModal(self):
            return choices.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    app = wx.App(False)
    frame = gui.MainFrame()
    try:
        monkeypatch.setattr(gui, "StoredSizeDialog", FakeStoredSizeDialog)
        sent, settings, shrunk = [], [], []
        monkeypatch.setattr(frame, "_send", lambda question, initial: sent.append(question))
        monkeypatch.setattr(frame, "change_chat_settings", lambda: settings.append(True))
        monkeypatch.setattr(frame, "shrink_stored_video", lambda: shrunk.append(True) or True)
        frame.config = gui.replace(frame.config, model="a/one")
        frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=video, title="V", model="",
                                                   initial_question="?")
        frame.question.SetValue("What happens?")
        choices[:] = [wx.ID_CANCEL, gui.SIZE_CHAT_SETTINGS]
        frame.on_send(None)
        frame.on_send(None)
        # Cancelled, then the chat settings opened: nothing was sent and the question is still there.
        assert sent == [] and settings == [True] and frame.question.GetValue() == "What happens?"
        choices[:] = [gui.SIZE_SHRINK]
        frame.on_send(None)
        assert shrunk == [True] and sent == ["What happens?"]
        choices[:] = [gui.SIZE_SEND]
        frame.question.SetValue("And then?")
        frame.on_send(None)
        assert sent[-1] == "And then?" and frame.size_skip_after_answer == (frame.current.id, "a/one")
        assert shown[0] == (frame.current.sent_video_size, "a/one") and len(shown) == 4
        # Once that answer arrived, the chat stops asking, until its Chat settings change.
        monkeypatch.setattr(gui, "announce", lambda *args, **kwargs: None)
        frame._completed(frame.current.id, "a/one")
        frame.question.SetValue("More?")
        frame.on_send(None)
        assert sent[-1] == "More?" and len(shown) == 4
        frame.store.update_chat_settings(frame.current.id, gui.OWNER_ID, "b/two", True)
        frame.current = frame.store.get_session(frame.current.id, gui.OWNER_ID)
        choices[:] = [wx.ID_CANCEL]
        frame.question.SetValue("With the new model?")
        frame.on_send(None)
        assert len(shown) == 5
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
