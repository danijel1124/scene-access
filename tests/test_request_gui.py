import pytest
import wx

from sceneaccess import gui
from sceneaccess.openrouter import Completion, Turn


@pytest.fixture
def frame(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path / 'config.json')
    monkeypatch.setattr(gui, 'announce', lambda *a, **kw: None)
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda *a, **kw: wx.OK)
    monkeypatch.setattr(gui.wx, 'CallAfter', lambda f, *a, **kw: f(*a, **kw))
    app = wx.App(False)
    window = gui.MainFrame()
    window.config = gui.replace(window.config, api_key='test-only')
    video = tmp_path / 'fixture.mp4'
    video.write_bytes(b'fixture, no media decoding')
    window.current = window.store.create_session(owner_id=gui.OWNER_ID, source_path=video,
        title='A', model='fixture/model', initial_question='Original question')
    yield window
    window.Destroy()
    app.Destroy()


class InlineThread:
    def __init__(self, target, **kw): self.target = target
    def start(self): self.target()


def test_revised_question_after_initial_failure_is_sent_and_persisted(frame, monkeypatch):
    monkeypatch.setattr(gui.threading, 'Thread', InlineThread)
    seen = []
    def answer(client, session, messages, *args, **kwargs):
        seen.extend(messages)
        return Turn(entries=[{'role':'assistant','content':'Answer'}], completion=Completion('Answer','fixture',1,None,None))
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answer)
    frame.question.SetValue('Revised question')
    frame.on_send(None)
    assert seen[0]['content'][0]['text'] == 'Revised question'
    assert frame.current.initial_question == 'Revised question'


def test_missing_api_key_preserves_question(frame):
    frame.config = gui.replace(frame.config, api_key="")
    frame.question.SetValue("Keep this question")
    frame.on_send(None)
    assert frame.question.GetValue() == "Keep this question"


def test_failed_followup_restores_question_for_retry(frame, monkeypatch):
    monkeypatch.setattr(gui.threading, 'Thread', InlineThread)
    frame.store.record_turn(frame.current.id, gui.OWNER_ID, [{'role':'assistant','content':'First answer'}])
    frame.current = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    def fail(*args, **kw): raise RuntimeError('Fixture provider failure')
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', fail)
    frame.question.SetValue('Do not lose me')
    frame.on_send(None)
    assert frame.question.GetValue() == 'Do not lose me'


def test_completion_for_other_chat_does_not_switch_active_chat(frame):
    a = frame.current
    b = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=gui.Path(a.source_path),
        title='B', model='fixture/model', initial_question='Other question')
    frame.current = b
    frame.render_current()
    frame._completed(a.id, 'fixture')
    assert frame.current.id == b.id


def test_request_buttons_cancel_and_pause_without_sending_another_question(frame, monkeypatch):
    monkeypatch.setattr(InlineThread, 'start', lambda self: None)
    monkeypatch.setattr(gui.threading, 'Thread', InlineThread)
    frame._send('Original question', initial=True)
    assert frame.send_button.GetLabel() == 'Cancel request'
    assert frame.send_button.IsEnabled()
    assert frame.pause_button.IsShown()
    frame.on_pause(None)
    assert frame._request.pause_requested
    frame.on_send(None)
    with pytest.raises(gui.RequestCancelled):
        frame._request.check_cancelled()
    frame._cancelled()
    assert frame.send_button.GetLabel() == 'Send question'
    assert not frame.pause_button.IsShown()


def test_shared_settings_replace_catalog_in_every_window(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    app = wx.App(False)
    manager = gui.ChatWindows()
    try:
        first, second = manager.new_window(), manager.new_window()
        old_catalog = second.catalog
        first.config = gui.replace(first.config, base_url='https://fixture.example/api/chat/completions')
        assert second.catalog.url == 'https://fixture.example/api/models'
        assert first.catalog is second.catalog
        assert old_catalog.url == 'https://openrouter.ai/api/v1/models'
    finally:
        for window in list(manager.frames): window.Destroy()
        app.Destroy()


def test_missing_session_does_not_create_blank_window(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    app = wx.App(False)
    manager = gui.ChatWindows()
    try:
        manager.new_window()
        assert manager.open_session('vid_missing') is None
        assert len(manager.frames) == 1
    finally:
        for window in list(manager.frames): window.Destroy()
        app.Destroy()


def test_separate_top_level_windows_reuse_same_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    app = wx.App(False)
    manager = None
    try:
        manager = gui.ChatWindows()
        launcher = manager.new_window()
        video = tmp_path/'fixture.mp4'
        video.write_bytes(b'fixture')
        sessions = [launcher.store.create_session(owner_id=gui.OWNER_ID, source_path=video,
                    title=name, model='fixture', initial_question='?') for name in ['A','B']]
        first = manager.open_session(sessions[0].id)
        second = manager.open_session(sessions[1].id)
        assert first is not second and first is not launcher
        assert first.GetParent() is None and second.GetParent() is None
        assert manager.open_session(sessions[0].id) is first
        assert len(manager.frames) == 3
        assert 'A' in first.GetTitle() and 'B' in second.GetTitle()
        launcher.config = gui.replace(launcher.config, max_tool_rounds=12)
        assert first.config.max_tool_rounds == 12
        assert second.config.max_tool_rounds == 12
        monkeypatch.setattr(gui.MainFrame, '_send', lambda *a, **kw: None)
        launcher.pending_size_skip = 'fixture/model'
        launcher._start_chat(video, 'New', 'Question', '', disable_tools=True)
        created = manager.frames[-1]
        assert created is not launcher and launcher.current is None
        assert created.current.tools == []
        assert created.size_skip_after_answer == (created.current.id, 'fixture/model')
        assert launcher.pending_size_skip == ''
    finally:
        if manager:
            for window in list(manager.frames): window.Destroy()
        app.Destroy()
