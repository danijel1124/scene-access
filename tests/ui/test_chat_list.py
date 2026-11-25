from dataclasses import replace

import pytest
import wx

from sceneaccess.ui import chat_list, gui


@pytest.fixture
def windows(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path / 'config.json')
    app = wx.App(False)
    manager = gui.ChatWindows()
    manager.show_list()
    manager.video = tmp_path / 'fixture.mp4'
    manager.video.write_bytes(b'fixture, no media decoding')
    manager.messages = []
    monkeypatch.setattr(wx, 'MessageBox', lambda message, *a, **kw: manager.messages.append(message) or wx.YES)
    yield manager
    for window in list(manager.all_windows()):
        window.Destroy()
    app.Destroy()


def add_chats(windows, *titles):
    sessions = [windows.store.create_session(owner_id=gui.OWNER_ID, source_path=windows.video, title=title,
                                             model='', initial_question='?') for title in titles]
    windows.refresh()
    return sessions


def list_event(listbox, kind, index):
    listbox.SetSelection(index)
    event = wx.CommandEvent(kind, listbox.GetId())
    event.SetEventObject(listbox)
    event.SetInt(index)
    listbox.GetEventHandler().ProcessEvent(event)


def select(windows, session_id):
    windows.main.sessions.SetSelection(windows.main.session_ids.index(session_id))


def test_moving_through_the_list_opens_nothing_and_enter_opens_a_chat_once(windows):
    add_chats(windows, 'A', 'B')
    main = windows.main
    for index in range(2):
        list_event(main.sessions, wx.wxEVT_LISTBOX, index)
    assert windows.frames == []
    # The first chat opens with no chat window open yet.
    assert main.handle_enter(main.sessions, wx.WXK_RETURN, shift=False) is True
    [frame] = windows.frames
    assert frame.current.id == main.session_ids[1] and frame.IsShown()
    assert 'A' in frame.GetTitle()
    main.handle_enter(main.sessions, wx.WXK_RETURN, shift=False)
    assert windows.frames == [frame]
    assert main.handle_enter(main.new_button, wx.WXK_RETURN, shift=False) is False


def test_a_double_click_opens_the_chat(windows):
    add_chats(windows, 'A')
    list_event(windows.main.sessions, wx.wxEVT_LISTBOX_DCLICK, 0)
    assert len(windows.frames) == 1 and windows.frames[0].IsShown()


def test_a_missing_chat_opens_no_window(windows):
    assert windows.open_session('vid_missing') is None
    assert windows.frames == []


def test_all_windows_share_settings_and_openrouters_catalog(windows):
    first, second = (windows.open_session(chat.id) for chat in add_chats(windows, 'A', 'B'))
    assert first is not second and first.GetParent() is None and second.GetParent() is None
    windows.main.config = replace(windows.main.config, max_tool_rounds=12, provider='llama.cpp',
                                  llama_cpp_url='http://fixture.example:8080/v1/chat/completions')
    assert first.config.max_tool_rounds == second.config.max_tool_rounds == 12
    assert first.catalog is second.catalog is windows.main.catalog
    assert windows.catalog.url == 'https://openrouter.ai/api/v1/models'


def test_a_new_chat_opens_in_its_own_chat_window(windows, monkeypatch):
    monkeypatch.setattr(gui.ChatFrame, '_send', lambda *a, **kw: None)
    main = windows.main
    main.pending_size_skip = 'fixture/model'
    main._start_chat(windows.video, 'New', 'Question', '', tools=())
    [created] = windows.frames
    assert created.IsShown() and created.current.title == 'New' and created.current.tools == []
    assert created.size_skip_after_answer == (created.current.id, 'fixture/model')
    assert main.pending_size_skip == ''
    assert main.sessions.GetStrings() == ['New'] and main.session_ids[main.sessions.GetSelection()] == created.current.id


def test_refreshing_the_list_keeps_the_selected_chat(windows):
    first = add_chats(windows, 'A', 'B')[0]
    select(windows, first.id)
    # E.g. an answer arriving in another chat window refreshes the list.
    windows.refresh()
    assert windows.main.session_ids[windows.main.sessions.GetSelection()] == first.id
    add_chats(windows, 'C')
    assert windows.main.session_ids[windows.main.sessions.GetSelection()] == first.id


def test_a_chat_that_fails_to_start_never_shows_its_window(windows, monkeypatch):
    shown = []
    monkeypatch.setattr(gui.ChatFrame, 'Show', lambda self, show=True: shown.append(self) or True)
    def fail(**kwargs): raise OSError('fixture: unreadable video')
    monkeypatch.setattr(windows.store, 'create_session', fail)
    windows.main._start_chat(windows.video, 'New', 'Question', '')
    assert shown == [] and windows.frames == []
    assert 'unreadable video' in windows.messages[-1]


def test_closing_the_list_closes_every_chat_and_cancels_its_request(windows):
    frame = windows.open_session(add_chats(windows, 'A')[0].id)
    control = frame._request = gui.RequestControl()
    windows.main.Close()
    assert control._cancelled and windows.frames == []


def test_a_busy_chat_is_not_deleted(windows):
    chat = add_chats(windows, 'A')[0]
    windows.requests[chat.id] = gui.RequestControl()
    select(windows, chat.id)
    windows.main.on_delete(None)
    assert 'still answering' in windows.messages[-1]
    assert windows.store.get_session(chat.id, gui.OWNER_ID)


def test_deleting_closes_the_chat_window_and_selects_a_neighbour(windows):
    kept, deleted = add_chats(windows, 'Kept', 'Deleted')
    frame = windows.open_session(deleted.id)
    select(windows, deleted.id)
    windows.main.on_delete(None)
    assert frame not in windows.frames
    assert windows.main.session_ids == [kept.id] and windows.main.sessions.GetSelection() == 0
    with pytest.raises(gui.SessionNotFound):
        windows.store.get_session(deleted.id, gui.OWNER_ID)


def test_a_failed_delete_keeps_the_chat_and_reports_why(windows, monkeypatch):
    chat = add_chats(windows, 'A')[0]
    def fail(*a, **kw): raise PermissionError('fixture: file is locked')
    monkeypatch.setattr(windows.store, 'delete_session', fail)
    select(windows, chat.id)
    windows.main.on_delete(None)
    assert 'file is locked' in windows.messages[-1]
    assert windows.main.session_ids == [chat.id]


def test_saved_settings_show_in_open_chat_windows(windows, monkeypatch):
    frame = windows.open_session(add_chats(windows, 'A')[0].id)
    changed = replace(windows.config, model='changed/model')

    class Dialog:
        def __init__(self, *args): pass
        def ShowModal(self): return wx.ID_OK
        def value(self): return changed
        def Destroy(self): pass

    monkeypatch.setattr(chat_list, 'SettingsDialog', Dialog)
    windows.main.on_settings(None)
    assert 'changed/model (from Settings)' in frame.status.GetLabel()
