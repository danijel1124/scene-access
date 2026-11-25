import threading
import time

import pytest
import wx

from sceneaccess.pipeline import answering
from sceneaccess.media import transcript
from sceneaccess.ui import app, app_window, chat_setup
from sceneaccess.llm.client import Completion, Turn
from dataclasses import replace
from sceneaccess.llm.client import ChatClient
from sceneaccess.request_control import RequestControl
from sceneaccess.storage.session_store import OWNER_ID, TRANSCRIPT_DONE, TRANSCRIPT_PENDING


def test_reopened_chat_waits_for_old_worker_and_refreshes_after_save(tmp_path, monkeypatch):
    monkeypatch.setattr(app_window, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    notices = []
    monkeypatch.setattr(wx, 'MessageBox', lambda message, *a, **kw: notices.append(message) or wx.OK)
    monkeypatch.setattr(answering, 'wants_transcript', lambda *a: False)
    monkeypatch.setattr(ChatClient, 'run_turn', lambda *a, **kw:
                        Turn(entries=[{'role':'assistant','content':'Saved answer'}],
                             completion=Completion('Saved answer','fixture',0,None,None)))
    wx_app = wx.App(False)
    windows = app.ChatWindows()
    entered, release, saved = threading.Event(), threading.Event(), threading.Event()
    try:
        windows.show_list()  # Keep the chat list alive after the chat closes.
        old = windows.new_chat_window()
        old.config = replace(old.config, api_key='test-only')
        source = tmp_path/'source.mp4'
        source.write_bytes(b'fixture')
        old.current = old.store.create_session(owner_id=OWNER_ID, source_path=source,
                                               title='Chat', model='fixture', initial_question='?')
        sid = old.current.id
        original = old.store.record_turn
        def paused_save(*args):
            entered.set()
            assert release.wait(5)
            original(*args)
            saved.set()
        monkeypatch.setattr(old.store, 'record_turn', paused_save)
        old._send('first', initial=False)
        assert entered.wait(3)
        old.Close()
        wx.Yield()
        reopened = windows.open_session(sid)
        reopened._send('second', initial=False)
        assert reopened._request is None
        assert reopened.question.GetValue() == 'second'
        assert notices and 'previous request' in notices[-1].lower()
        release.set()
        assert saved.wait(3)
        deadline = time.monotonic()+3
        while time.monotonic() < deadline and sid in windows.requests:
            wx.Yield()
            time.sleep(.01)
        wx.Yield()
        assert sid not in windows.requests
        assert any(item['content']=='Saved answer' for item in reopened.current.history)
    finally:
        release.set()
        saved.wait(3)
        wx.Yield()
        for frame in list(windows.all_windows()): frame.Destroy()
        wx_app.Destroy()


def test_reopened_setup_waits_for_final_transcript_write(tmp_path, monkeypatch):
    monkeypatch.setattr(app_window, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    monkeypatch.setattr(answering, 'wants_transcript', lambda *a: True)
    monkeypatch.setattr(transcript, 'make_transcript', lambda *a: '[00:00] Old transcript')
    monkeypatch.setattr(ChatClient, 'run_turn', lambda *a, **kw:
                        Turn(entries=[{'role': 'assistant', 'content': 'Must not be saved'}],
                             completion=Completion('Must not be saved', 'fixture', 0, None, None)))
    wx_app = wx.App(False)
    windows = app.ChatWindows()
    entered, release, saved = threading.Event(), threading.Event(), threading.Event()
    try:
        windows.show_list()
        old = windows.new_chat_window()
        old.config = replace(old.config, api_key='test-only')
        source = tmp_path/'source.mp4'
        source.write_bytes(b'fixture')
        old.current = old.store.create_session(owner_id=OWNER_ID, source_path=source,
                                               title='Chat', model='fixture', initial_question='?',
                                               transcript_state=TRANSCRIPT_PENDING)
        sid = old.current.id
        original = old.store.update_transcript

        def delayed_write(*args):
            # Cancellation can arrive after the transcript returns, while its save is in flight.
            entered.set()
            assert release.wait(5)
            original(*args)
            saved.set()

        monkeypatch.setattr(old.store, 'update_transcript', delayed_write)
        old._send('first', initial=False)
        assert entered.wait(3)
        old.Close()
        wx.Yield()
        reopened = windows.open_session(sid)
        assert reopened is not None
        assert reopened._request is None
        assert sid in windows.requests
        for button in (reopened.setup_button, reopened.chat_settings_button, reopened.update_path_button):
            assert not button.IsEnabled()
        reopened.apply_setup('General description')
        assert reopened.store.get_session(sid, OWNER_ID).system_prompt == ''
        release.set()
        assert saved.wait(3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and sid in windows.requests:
            wx.Yield()
            time.sleep(.01)
        wx.Yield()
        assert sid not in windows.requests
        assert reopened.setup_button.IsEnabled()
        assert reopened.current.transcript == '[00:00] Old transcript'
        assert reopened.current.history == []  # Cancellation still prevents saving the answer.
        reopened.apply_setup('General description')
        persisted = reopened.store.get_session(sid, OWNER_ID)
        assert persisted.system_prompt
        # A new setup keeps the finished transcript, so it is never paid for twice.
        assert persisted.transcript_state == TRANSCRIPT_DONE
        assert persisted.transcript == '[00:00] Old transcript'
    finally:
        release.set()
        saved.wait(3)
        deadline = time.monotonic() + 3
        while windows.requests and time.monotonic() < deadline:
            wx.Yield()
            time.sleep(.01)
        for frame in list(windows.all_windows()):
            frame.Destroy()
        wx_app.Destroy()


@pytest.mark.parametrize('reopen', [False, True], ids=['active', 'reopened'])
@pytest.mark.parametrize('handler', [
    'change_setup', 'apply_setup', 'update_system_prompt', 'on_update_path', 'change_chat_settings',
    '_chat_settings_loaded', 'shrink_stored_video', '_find_online_source',
])
def test_busy_chat_rejects_direct_mutation_handlers(tmp_path, monkeypatch, reopen, handler):
    monkeypatch.setattr(app_window, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')

    def forbidden(*a, **kw):
        pytest.fail(f'{handler} reached mutation work while the chat worker was still running')

    wx_app = wx.App(False)
    windows = app.ChatWindows()
    try:
        windows.show_list()
        old = windows.new_chat_window()
        source = tmp_path/'source.mp4'
        source.write_bytes(b'fixture')
        old.current = old.store.create_session(owner_id=OWNER_ID, source_path=source,
                                               title='Chat', model='fixture', initial_question='?')
        sid = old.current.id
        control = old._request = RequestControl()
        windows.requests[sid] = control
        old._set_request_buttons(True)
        if reopen:
            old.Close()
            wx.Yield()
            frame = windows.open_session(sid)
        else:
            frame = old
        assert frame is not None
        before = frame.store.get_session(sid, OWNER_ID)
        for name in ('SingleChoiceDialog', 'FileDialog', 'MessageBox'):
            monkeypatch.setattr(wx, name, forbidden)
        monkeypatch.setattr(chat_setup, 'ChatSettingsDialog', forbidden)
        monkeypatch.setattr(frame.catalog, 'model_ids', forbidden)
        monkeypatch.setattr(frame.store, 'update_setup', forbidden)
        monkeypatch.setattr(frame.store, 'update_system_prompt', forbidden)
        monkeypatch.setattr(chat_setup, 'local_video', forbidden)
        monkeypatch.setattr(frame, '_with_youtube_sign_in', forbidden)
        args = {'apply_setup': ('General description',), 'on_update_path': (None,),
                '_chat_settings_loaded': (sid, {})}.get(handler, ())
        getattr(frame, handler)(*args)
        assert frame.store.get_session(sid, OWNER_ID) == before
        for button in (frame.setup_button, frame.prompt_button, frame.chat_settings_button, frame.update_path_button):
            assert not button.IsEnabled()
        if not reopen:
            # Completion callbacks precede request_finished: do not unlock writes too early.
            frame._finish_request()
            assert not frame.setup_button.IsEnabled()
        windows.request_finished(sid, control)
        for button in (frame.setup_button, frame.chat_settings_button, frame.update_path_button):
            assert button.IsEnabled()
    finally:
        for frame in list(windows.all_windows()):
            frame.Destroy()
        wx_app.Destroy()


def test_reopened_mutations_work_after_worker_finishes_without_blocking_other_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(app_window, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    wx_app = wx.App(False)
    windows = app.ChatWindows()
    try:
        launcher = windows.show_list()
        source = tmp_path/'source.mp4'
        source.write_bytes(b'original video')
        session = launcher.store.create_session(owner_id=OWNER_ID, source_path=source,
                                                title='Chat', model='fixture', initial_question='?')
        other = launcher.store.create_session(owner_id=OWNER_ID, source_path=source,
                                              title='Other', model='fixture', initial_question='?')
        control = RequestControl()
        control.cancel()
        windows.requests[session.id] = control
        frame = windows.open_session(session.id)
        independent = windows.open_session(other.id)
        assert frame is not None and independent is not None
        independent.apply_setup('General description')
        assert independent.current.system_prompt
        assert independent.setup_button.IsEnabled()
        assert not frame.setup_button.IsEnabled()
        # A stale completion must not unlock the newer registered request.
        windows.request_finished(session.id, RequestControl())
        assert windows.requests[session.id] is control
        assert not frame.setup_button.IsEnabled()
        frame._loading_chat_settings = True
        windows.request_finished(session.id, control)
        assert frame.setup_button.IsEnabled()
        assert not frame.chat_settings_button.IsEnabled()  # Its own model lookup is still pending.

        class SettingsDialog:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def ShowModal(self): return wx.ID_OK
            def value(self): return 'changed/model', True, False
            def provider_value(self): return 'openrouter', False

        monkeypatch.setattr(chat_setup, 'ChatSettingsDialog', SettingsDialog)
        frame._chat_settings_loaded(session.id, {'openrouter': ['changed/model'], 'llama.cpp': []})
        assert frame.chat_settings_button.IsEnabled()
        assert frame.current.model == 'changed/model'
        assert frame.current.audio_only

        replacement = tmp_path/'moved.mp4'
        replacement.write_bytes(b'relocated video')

        class PathDialog(SettingsDialog):
            def GetPath(self): return str(replacement)

        monkeypatch.setattr(wx, 'FileDialog', PathDialog)
        frame.on_update_path(None)
        assert frame.current.source_path == str(replacement)

        shrink_dir = tmp_path/'shrink'
        shrink_dir.mkdir()
        shrunk = shrink_dir/'small.mp4'
        shrunk.write_bytes(b'small')
        original_size = frame.current.sent_video_size
        monkeypatch.setattr(frame, '_shrink_with_progress', lambda _: shrunk)
        assert frame.shrink_stored_video()
        persisted = frame.store.get_session(session.id, OWNER_ID)
        assert persisted.sent_video_size < original_size
        assert persisted.model == 'changed/model'
        assert persisted.source_path == str(replacement)
    finally:
        for frame in list(windows.all_windows()):
            frame.Destroy()
        wx_app.Destroy()
