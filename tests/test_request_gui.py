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
    sent = []
    def answer(client, session, messages, *args, **kwargs):
        sent.append(messages[0]['content'][0]['text'])
        if len(sent) == 1:
            raise RuntimeError('Fixture provider failure')
        return Turn(entries=[{'role':'assistant','content':'Answer'}], completion=Completion('Answer','fixture',1,None,None))
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answer)
    frame.question.SetValue('Original question')
    frame.on_send(None)
    # The first request failed: nothing was answered and the question is back in the field.
    assert frame.current.history == [] and frame.question.GetValue() == 'Original question'
    frame.question.SetValue('Revised question')
    frame.on_send(None)
    assert sent == ['Original question', 'Revised question']
    stored = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    assert stored.initial_question == 'Revised question'
    assert [item['content'] for item in stored.history] == ['Answer']


def test_audio_only_routing_failure_does_not_transcribe(frame, monkeypatch):
    from sceneaccess.session_store import TRANSCRIPT_PENDING
    monkeypatch.setattr(gui.threading, 'Thread', InlineThread)
    frame.store.update_chat_settings(frame.current.id, gui.OWNER_ID, 'openrouter/auto', True)
    frame.store.update_transcript(frame.current.id, gui.OWNER_ID, TRANSCRIPT_PENDING)
    frame.current = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    class Unavailable:
        def audio_video_tool_models(self): return []
        def hears_audio(self, model): return None
    frame.catalog = Unavailable()
    called = []
    errors = []
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, *a, **kw: errors.append(message))
    monkeypatch.setattr(gui, 'make_transcript', lambda *a, **kw: called.append(True) or 'Unnecessary')
    frame._send('Original question', initial=True)
    assert called == []
    assert errors and 'model list could not be loaded' in errors[-1]


@pytest.mark.parametrize('outcome', ['failure', 'cancel', 'success'])
def test_turn_frames_are_committed_only_with_successful_history(frame, monkeypatch, outcome):
    import subprocess
    import types
    import imageio_ffmpeg
    from sceneaccess.tools import ZOOM_FRAME, tool_definitions
    # Only gui's own thread runs inline: the real tool starts subprocesses, whose reader threads
    # need the real threading.Thread.
    monkeypatch.setattr(gui, 'threading', types.SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(gui, 'needs_transcript', lambda *a: False)
    video = gui.Path(frame.current.source_path).with_name('real.mp4')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc=duration=1:size=160x120:rate=5', '-pix_fmt', 'yuv420p', '-y', str(video)], check=True)
    frame.current = frame.store.create_session(owner_id=gui.OWNER_ID, source_path=video, title='Real',
        model='fixture/model', initial_question='Original question', tools=tool_definitions(online_video=False))
    generated = []
    def answer(client, session, messages, run_tool, *args, **kwargs):
        # The real zoom tool makes the frame, so the test relies on the tool registering it.
        result = run_tool(ZOOM_FRAME, '{"time": 0}')
        assert result.image, result.text
        generated.append(session.directory/result.image)
        if outcome == 'failure': raise RuntimeError('provider fixture failed')
        if outcome == 'cancel': raise gui.RequestCancelled()
        return Turn(entries=[{'role': 'assistant', 'content': 'Answer'}],
                    completion=Completion('Answer', 'fixture', 1, None, None))
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answer)
    errors = []
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, *a, **kw: errors.append(message))
    frame._send('Original question', initial=True)
    assert generated, errors
    assert generated[0].exists() is (outcome == 'success')


def test_delete_error_keeps_chat_visible_and_reports_failure(frame, monkeypatch):
    messages = []
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, *a, **kw: messages.append(message) or wx.YES)
    def fail(*a, **kw): raise PermissionError('fixture: file is locked')
    monkeypatch.setattr(frame.store, 'delete_session', fail)
    session_id = frame.current.id
    frame.on_delete(None)
    assert frame.current.id == session_id
    assert 'file is locked' in messages[-1]


def test_excluded_transcription_provider_is_never_called(frame, monkeypatch):
    from sceneaccess.session_store import TRANSCRIPT_PENDING, TRANSCRIPT_FAILED
    monkeypatch.setattr(gui.threading, 'Thread', InlineThread)
    monkeypatch.setattr(gui, 'needs_transcript', lambda *a: True)
    frame.config = gui.replace(frame.config, ignored_providers=('elevenlabs',))
    frame.store.update_transcript(frame.current.id, gui.OWNER_ID, TRANSCRIPT_PENDING)
    frame.current = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    transcriptions, requests = [], []
    monkeypatch.setattr(gui, 'make_transcript', lambda *a: transcriptions.append(True) or 'should not run')
    def answer(*args, **kwargs):
        requests.append(True)
        return Turn(entries=[{'role':'assistant','content':'Picture-only answer'}],
                    completion=Completion('Picture-only answer','fixture',1,None,None))
    monkeypatch.setattr(gui.OpenRouterClient,'run_turn',answer)
    frame._send('Original question',initial=True)
    assert requests == [True] and not transcriptions
    saved = frame.store.get_session(frame.current.id,gui.OWNER_ID)
    assert saved.transcript_state == TRANSCRIPT_FAILED
    assert 'excluded' in saved.transcript.lower() and 'elevenlabs' in saved.transcript.lower()
    entries = gui.chat_history_entries(saved.initial_question,saved.history,saved.transcript_state,saved.transcript)
    assert any('excluded' in text.lower() for text, _ in entries)


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


def test_a_background_answer_never_takes_the_focus(frame, monkeypatch):
    frame.store.record_turn(frame.current.id, gui.OWNER_ID, [{'role': 'assistant', 'content': 'Answer'}])
    focused, spoken = [], []
    monkeypatch.setattr(frame.chat_list, 'SetFocus', lambda: focused.append(True))
    monkeypatch.setattr(gui, 'announce', lambda text, **kw: spoken.append(text))
    # Another chat window or application is active: neither the focus nor the screen reader is taken.
    monkeypatch.setattr(frame, 'IsActive', lambda: False)
    frame._completed(frame.current.id, 'fixture')
    assert focused == [] and spoken == []
    # This window is active: the focus moves to the answer, so the screen reader reads it.
    monkeypatch.setattr(frame, 'IsActive', lambda: True)
    monkeypatch.setattr(gui.wx.Window, 'FindFocus', staticmethod(lambda: frame.question))
    frame._completed(frame.current.id, 'fixture')
    assert focused == [True] and spoken == []
    # The list already has the focus, so moving it would read nothing: the answer is announced.
    monkeypatch.setattr(gui.wx.Window, 'FindFocus', staticmethod(lambda: frame.chat_list))
    frame._completed(frame.current.id, 'fixture')
    assert focused == [True] and spoken == ['Model: Answer']


def test_the_newest_answer_is_selected_not_its_usage_line(frame):
    frame.store.record_turn(frame.current.id, gui.OWNER_ID, [
        {'role': 'assistant', 'content': 'First', 'usage': {'model': 'm', 'prompt_tokens': 1}},
        {'role': 'user', 'content': 'More?'},
        {'role': 'assistant', 'content': 'Second', 'usage': {'model': 'm', 'prompt_tokens': 2}}])
    frame.current = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    frame.render_current()
    assert frame.chat_list.GetStringSelection() == 'Model: Second'
    assert frame.chat_list.GetString(frame.chat_list.GetCount() - 1).startswith('Usage:')


def test_a_failed_question_comes_back_only_into_an_empty_field(frame):
    frame._pending_question = 'Failed question'
    frame.question.SetValue('A new question typed meanwhile')
    frame._failed('Fixture provider failure')
    assert frame.question.GetValue() == 'A new question typed meanwhile'
    frame._pending_question = 'Failed question'
    frame.question.SetValue('')
    frame._failed('Fixture provider failure')
    assert frame.question.GetValue() == 'Failed question'


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
