import threading
from dataclasses import replace
from pathlib import Path

import pytest
import wx

from sceneaccess.llm.client import ChatClient, Completion, Turn
from sceneaccess.media import transcript
from sceneaccess.pipeline import answering
from sceneaccess.request_control import RequestCancelled
from sceneaccess.storage.session_store import OWNER_ID
from sceneaccess.ui import app_window, chat_request, chat_window, speech
from sceneaccess.ui.chat_history import chat_history_entries


@pytest.fixture
def frame(tmp_path, monkeypatch):
    monkeypatch.setattr(app_window, 'config_path_from_standard_paths', lambda _: tmp_path / 'config.json')
    monkeypatch.setattr(wx, 'MessageBox', lambda *a, **kw: wx.OK)
    monkeypatch.setattr(wx, 'CallAfter', lambda f, *a, **kw: f(*a, **kw))
    wx_app = wx.App(False)
    window = chat_window.ChatFrame()
    window.config = replace(window.config, api_key='test-only')
    video = tmp_path / 'fixture.mp4'
    video.write_bytes(b'fixture, no media decoding')
    window.current = window.store.create_session(owner_id=OWNER_ID, source_path=video,
        title='A', model='fixture/model', initial_question='Original question')
    yield window
    window.Destroy()
    wx_app.Destroy()


class InlineThread:
    def __init__(self, target, **kw): self.target = target
    def start(self): self.target()


def test_revised_question_after_initial_failure_is_sent_and_persisted(frame, monkeypatch):
    monkeypatch.setattr(threading, 'Thread', InlineThread)
    sent = []
    def answer(client, session, messages, *args, **kwargs):
        # The question is the first text, after the video.
        sent.append(next(part['text'] for part in messages[0]['content'] if part['type'] == 'text'))
        if len(sent) == 1:
            raise RuntimeError('Fixture provider failure')
        return Turn(entries=[{'role':'assistant','content':'Answer'}], completion=Completion('Answer','fixture',1,None,None))
    monkeypatch.setattr(ChatClient, 'run_turn', answer)
    frame.question.SetValue('Original question')
    frame.on_send(None)
    # The first request failed: nothing was answered and the question is back in the field.
    assert frame.current.history == [] and frame.question.GetValue() == 'Original question'
    frame.question.SetValue('Revised question')
    frame.on_send(None)
    assert sent == ['Original question', 'Revised question']
    stored = frame.store.get_session(frame.current.id, OWNER_ID)
    assert stored.initial_question == 'Revised question'
    assert [item['content'] for item in stored.history] == ['Answer']


def test_audio_only_routing_failure_does_not_transcribe(frame, monkeypatch):
    from sceneaccess.storage.session_store import TRANSCRIPT_PENDING
    monkeypatch.setattr(threading, 'Thread', InlineThread)
    frame.store.update_chat_settings(frame.current.id, OWNER_ID, 'openrouter/auto', True)
    frame.store.update_transcript(frame.current.id, OWNER_ID, TRANSCRIPT_PENDING)
    frame.current = frame.store.get_session(frame.current.id, OWNER_ID)
    class Unavailable:
        def audio_video_tool_models(self): return []
        def hears_audio(self, model): return None
    frame.catalog = Unavailable()
    called = []
    errors = []
    monkeypatch.setattr(wx, 'MessageBox', lambda message, *a, **kw: errors.append(message))
    monkeypatch.setattr(transcript, 'make_transcript', lambda *a, **kw: called.append(True) or 'Unnecessary')
    frame._send('Original question', initial=True)
    assert called == []
    assert errors and 'model list could not be loaded' in errors[-1]


@pytest.mark.parametrize('outcome', ['failure', 'cancel', 'success'])
def test_turn_frames_are_committed_only_with_successful_history(frame, monkeypatch, outcome):
    import subprocess
    import types

    import imageio_ffmpeg

    from sceneaccess.llm.tool_calls import ZOOM_FRAME
    from sceneaccess.pipeline.tools import tool_definitions
    # Only the request's own thread runs inline: the real tool starts subprocesses, whose reader threads
    # need the real threading.Thread.
    monkeypatch.setattr(chat_request, 'threading', types.SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(answering, 'wants_transcript', lambda *a: False)
    video = Path(frame.current.source_path).with_name('real.mp4')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc=duration=1:size=160x120:rate=5', '-pix_fmt', 'yuv420p', '-y', str(video)], check=True)
    frame.current = frame.store.create_session(owner_id=OWNER_ID, source_path=video, title='Real',
        model='fixture/model', initial_question='Original question', tools=tool_definitions(online_video=False))
    generated = []
    def answer(client, session, messages, run_tool, *args, **kwargs):
        # The real zoom tool makes the frame, so the test relies on the tool registering it.
        result = run_tool(ZOOM_FRAME, '{"time": 0}')
        assert result.image, result.text
        generated.append(session.directory/result.image)
        if outcome == 'failure': raise RuntimeError('provider fixture failed')
        if outcome == 'cancel': raise RequestCancelled()
        return Turn(entries=[{'role': 'assistant', 'content': 'Answer'}],
                    completion=Completion('Answer', 'fixture', 1, None, None))
    monkeypatch.setattr(ChatClient, 'run_turn', answer)
    errors = []
    monkeypatch.setattr(wx, 'MessageBox', lambda message, *a, **kw: errors.append(message))
    frame._send('Original question', initial=True)
    assert generated, errors
    assert generated[0].exists() is (outcome == 'success')


def test_excluded_transcription_provider_is_never_called(frame, monkeypatch):
    from sceneaccess.storage.session_store import TRANSCRIPT_FAILED, TRANSCRIPT_PENDING
    monkeypatch.setattr(threading, 'Thread', InlineThread)
    monkeypatch.setattr(answering, 'wants_transcript', lambda *a: True)
    frame.config = replace(frame.config, ignored_providers=('elevenlabs',))
    frame.store.update_transcript(frame.current.id, OWNER_ID, TRANSCRIPT_PENDING)
    frame.current = frame.store.get_session(frame.current.id, OWNER_ID)
    transcriptions, requests = [], []
    monkeypatch.setattr(transcript, 'make_transcript', lambda *a: transcriptions.append(True) or 'should not run')
    def answer(*args, **kwargs):
        requests.append(True)
        return Turn(entries=[{'role':'assistant','content':'Picture-only answer'}],
                    completion=Completion('Picture-only answer','fixture',1,None,None))
    monkeypatch.setattr(ChatClient,'run_turn',answer)
    frame._send('Original question',initial=True)
    assert requests == [True] and not transcriptions
    saved = frame.store.get_session(frame.current.id,OWNER_ID)
    assert saved.transcript_state == TRANSCRIPT_FAILED
    assert 'excluded' in saved.transcript.lower() and 'elevenlabs' in saved.transcript.lower()
    entries = chat_history_entries(saved.initial_question,saved.history,saved.transcript_state,saved.transcript)
    assert any('excluded' in text.lower() for text, _ in entries)


@pytest.mark.parametrize('cancelled_in', ['moments', 'answer'])
def test_a_cancel_while_splitting_into_moments_keeps_the_answer_but_not_during_it(frame, monkeypatch, cancelled_in):
    from sceneaccess.storage.session_store import MOMENTS
    monkeypatch.setattr(chat_request.threading, 'Thread', InlineThread)
    monkeypatch.setattr(answering, 'wants_transcript', lambda *a: False)
    # A real length, so the step that splits the answer into moments runs.
    monkeypatch.setattr(answering, 'video_duration', lambda session: 84.0)

    def answer(client, session, messages, run_tool, on_event=None, routing=None, max_tool_rounds=6, **kwargs):
        if (max_tool_rounds == 0) == (cancelled_in == 'moments'):
            frame._request.cancel()
            raise RequestCancelled()
        return Turn(entries=[{'role': 'assistant', 'content': '00:00 She smiles.'}],
                    completion=Completion('00:00 She smiles.', 'fixture', 1, None, None))

    monkeypatch.setattr(ChatClient, 'run_turn', answer)
    frame._send('Original question', initial=True)
    stored = frame.store.get_session(frame.current.id, OWNER_ID)
    if cancelled_in == 'moments':
        assert [entry['content'] for entry in stored.history] == ['00:00 She smiles.']
        assert MOMENTS not in stored.history[-1]
        # The cancel the user asked for is shown, though the answer is kept.
        assert 'Request cancelled while splitting the answer into moments' in frame.status.GetLabel()
    else:
        assert stored.history == []
    assert frame._request is None and frame.send_button.GetLabel() == 'Send question'


def test_missing_api_key_preserves_question(frame):
    frame.config = replace(frame.config, api_key="")
    frame.question.SetValue("Keep this question")
    frame.on_send(None)
    assert frame.question.GetValue() == "Keep this question"


def test_failed_followup_restores_question_for_retry(frame, monkeypatch):
    monkeypatch.setattr(threading, 'Thread', InlineThread)
    frame.store.record_turn(frame.current.id, OWNER_ID, [{'role':'assistant','content':'First answer'}])
    frame.current = frame.store.get_session(frame.current.id, OWNER_ID)
    def fail(*args, **kw): raise RuntimeError('Fixture provider failure')
    monkeypatch.setattr(ChatClient, 'run_turn', fail)
    frame.question.SetValue('Do not lose me')
    frame.on_send(None)
    assert frame.question.GetValue() == 'Do not lose me'


def test_completion_for_other_chat_does_not_switch_active_chat(frame):
    a = frame.current
    b = frame.store.create_session(owner_id=OWNER_ID, source_path=Path(a.source_path),
        title='B', model='fixture/model', initial_question='Other question')
    frame.current = b
    frame.render_current()
    frame._completed(a.id, 'fixture')
    assert frame.current.id == b.id


def test_request_buttons_cancel_and_pause_without_sending_another_question(frame, monkeypatch):
    monkeypatch.setattr(InlineThread, 'start', lambda self: None)
    monkeypatch.setattr(threading, 'Thread', InlineThread)
    frame._send('Original question', initial=True)
    assert frame.send_button.GetLabel() == 'Cancel request'
    assert frame.send_button.IsEnabled()
    assert frame.pause_button.IsShown()
    frame.on_pause(None)
    assert frame._request.pause_requested
    frame.on_send(None)
    with pytest.raises(RequestCancelled):
        frame._request.check_cancelled()
    frame._cancelled()
    assert frame.send_button.GetLabel() == 'Send question'
    assert not frame.pause_button.IsShown()


def test_a_background_answer_never_takes_the_focus(frame, monkeypatch):
    frame.store.record_turn(frame.current.id, OWNER_ID, [{'role': 'assistant', 'content': 'Answer'}])
    focused, spoken = [], []
    monkeypatch.setattr(frame.chat_list, 'SetFocus', lambda: focused.append(True))
    monkeypatch.setattr(speech, 'announce', lambda text, **kw: spoken.append(text))
    # Another chat window or application is active: neither the focus nor the screen reader is taken.
    monkeypatch.setattr(frame, 'IsActive', lambda: False)
    frame._completed(frame.current.id, 'fixture')
    assert focused == [] and spoken == []
    # This window is active: the focus moves to the answer, so the screen reader reads it.
    monkeypatch.setattr(frame, 'IsActive', lambda: True)
    monkeypatch.setattr(wx.Window, 'FindFocus', staticmethod(lambda: frame.question))
    frame._completed(frame.current.id, 'fixture')
    assert focused == [True] and spoken == []
    # The list already has the focus, so moving it would read nothing: the answer is announced.
    monkeypatch.setattr(wx.Window, 'FindFocus', staticmethod(lambda: frame.chat_list))
    frame._completed(frame.current.id, 'fixture')
    assert focused == [True] and spoken == ['Model: Answer']


def test_the_newest_answer_is_selected_not_its_usage_line(frame):
    frame.store.record_turn(frame.current.id, OWNER_ID, [
        {'role': 'assistant', 'content': 'First', 'usage': {'model': 'm', 'prompt_tokens': 1}},
        {'role': 'user', 'content': 'More?'},
        {'role': 'assistant', 'content': 'Second', 'usage': {'model': 'm', 'prompt_tokens': 2}}])
    frame.current = frame.store.get_session(frame.current.id, OWNER_ID)
    frame.render_current()
    assert list(frame.chat_list.GetSelections()) == [frame.history_caret()]
    assert frame.history_items[frame.history_caret()] == 'Model: Second'
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
