from pathlib import Path

import pytest
import wx

from sceneaccess import gui, new_chat


@pytest.mark.parametrize('cancel', [False, True])
def test_cached_file_metadata_failure_warns_but_cancel_stops_selection(tmp_path, monkeypatch, cancel):
    from types import SimpleNamespace
    from sceneaccess.config import AppConfig
    from sceneaccess.request_control import RequestCancelled
    source = tmp_path/'Video [abcdefghijk].mp4'
    source.write_bytes(b'existing video')
    url = 'https://www.youtube.com/watch?v=abcdefghijk'
    entry = {'url': url, 'title': 'Video', 'filename': source.name, 'info': {'title': 'Video'}}
    messages = []

    class Choice:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ShowModal(self): return wx.ID_OK
        def GetSelection(self): return 0

    class Harness(new_chat.NewChatMixin):
        config = AppConfig()
        owner_id = 'owner'
        store = SimpleNamespace(list_sessions=lambda owner: [])
        def _with_youtube_sign_in(self, call): return call('')
        def _lookup_with_progress(self, function, args, cookies):
            if function is new_chat.youtube_sources.fetch_video_info:
                if cancel: raise RequestCancelled()
                raise RuntimeError('Offline fixture')
            return {'is_playlist': True, 'entries': [entry]}

    monkeypatch.setattr(new_chat.wx, 'SingleChoiceDialog', Choice)
    monkeypatch.setattr(new_chat.wx, 'MessageBox', lambda message, *a: messages.append(message))
    result = Harness()._choose_youtube_video('playlist', tmp_path)
    if cancel:
        assert result is None and not messages
    else:
        assert result[1][0] == source and result[1][2] == {'title': 'Video'}
        assert len(messages) == 1 and 'Offline fixture' in messages[0]


@pytest.mark.parametrize('requested_height', [360, 480, 720])
def test_advanced_quality_change_replaces_cached_file_only_when_requested(tmp_path, monkeypatch, requested_height):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    monkeypatch.setattr(new_chat, 'url_from_clipboard', lambda: '')
    source = tmp_path/'old480.mp4'
    source.write_bytes(b'old video')
    app = wx.App(False)
    frame = gui.MainFrame()
    frame.config = gui.replace(frame.config, youtube_max_height=480)
    calls = []

    class LinkDialog:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ShowModal(self): return wx.ID_OK
        def GetValue(self): return 'https://www.youtube.com/watch?v=abcdefghijk'

    def ask(**kwargs):
        frame.config = gui.replace(frame.config, youtube_max_height=requested_height)
        return 'Describe', ''

    def download(url, folder, height):
        calls.append(height)
        path = folder/f'new{height}.mp4'
        path.write_bytes(b'new quality')
        return path, 'Video', {}

    monkeypatch.setattr(new_chat.wx, 'TextEntryDialog', LinkDialog)
    monkeypatch.setattr(frame, '_choose_youtube_video', lambda url, folder: (url, (source, 'Video', {})))
    monkeypatch.setattr(frame, '_download_dir', lambda: tmp_path)
    monkeypatch.setattr(frame, '_ask_first_question', ask)
    monkeypatch.setattr(frame, '_download_with_progress', download)
    monkeypatch.setattr(new_chat, 'video_height', lambda _: 480)
    monkeypatch.setattr(frame, '_send', lambda *a, **kw: None)
    try:
        frame.new_from_youtube()
        assert calls == ([] if requested_height == 480 else [requested_height])
        expected = source if requested_height == 480 else tmp_path/f'new{requested_height}.mp4'
        assert Path(frame.current.source_path) == expected
        assert source.read_bytes() == b'old video'
    finally:
        frame.Destroy()
        app.Destroy()


@pytest.mark.parametrize('selection', [0, 1, None])
@pytest.mark.parametrize('misleading_titles', [False, True])
def test_playlist_hides_existing_chats_reuses_orphans_and_downloads_only_selected(tmp_path, monkeypatch, selection, misleading_titles):
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path/'config.json')
    monkeypatch.setattr(new_chat, 'url_from_clipboard', lambda: '')
    app = wx.App(False)
    frame = gui.MainFrame()
    downloads = tmp_path/'downloads'
    downloads.mkdir()
    hidden = downloads/('Hidden [ccccccccccc] [aaaaaaaaaaa].mp4' if misleading_titles else 'Hidden [aaaaaaaaaaa].mp4')
    orphan = downloads/('Orphan [ccccccccccc] [bbbbbbbbbbb].mp4' if misleading_titles else 'Orphan [bbbbbbbbbbb].mp4')
    hidden.write_bytes(b'hidden video')
    orphan.write_bytes(b'orphan video')
    entries = [{'id': key, 'url': f'https://www.youtube.com/watch?v={key}', 'title': title, 'info': {}}
               for key, title in [('aaaaaaaaaaa', 'Hidden'), ('bbbbbbbbbbb', 'Orphan'), ('ccccccccccc', 'Missing')]]
    if misleading_titles:
        entries[1]['title'] = 'Orphan [ccccccccccc]'
    for entry in entries:
        entry['filename'] = f"{entry['title']} [{entry['id']}].mp4"
    frame.store.create_session(owner_id=frame.owner_id, source_path=hidden, title='Hidden', model='',
                               initial_question='?', source_url=entries[0]['url'])
    labels, requested, questions, sent, metadata_requests = [], [], [], [], []

    class LinkDialog:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ShowModal(self): return wx.ID_OK
        def GetValue(self): return 'https://www.youtube.com/playlist?list=fixture'

    class ChoiceDialog(LinkDialog):
        def __init__(self, parent, message, title, choices): labels.extend(choices)
        def ShowModal(self): return wx.ID_CANCEL if selection is None else wx.ID_OK
        def GetSelection(self): return selection

    def download(url, target, height):
        requested.append(url)
        path = target/'Missing [ccccccccccc].mp4'
        path.write_bytes(b'new video')
        return path, 'Missing', {}

    monkeypatch.setattr(new_chat.wx, 'TextEntryDialog', LinkDialog)
    monkeypatch.setattr(new_chat.wx, 'SingleChoiceDialog', ChoiceDialog)
    monkeypatch.setattr(frame, '_download_dir', lambda: downloads)
    def lookup(function, args, cookies):
        if function is new_chat.youtube_sources.fetch_video_info:
            metadata_requests.append(args[0])
            return {'description': 'Full description', 'tags': ['known product'],
                    'chapters': [{'start_time': 0, 'end_time': 1, 'title': 'Introduction'}]}
        return {'is_playlist': True, 'title': 'Playlist', 'entries': entries}
    monkeypatch.setattr(frame, '_lookup_with_progress', lookup)
    monkeypatch.setattr(new_chat.youtube_sources, 'list_video_choices', lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(frame, '_ask_first_question', lambda **kw: questions.append(True) or ('Describe', ''))
    monkeypatch.setattr(frame, '_download_with_progress', download)
    monkeypatch.setattr(new_chat, 'video_height', lambda _: 480)
    monkeypatch.setattr(frame, '_send', lambda *a, **kw: sent.append(frame.current.id))
    try:
        frame.new_from_youtube()
        assert len(labels) == 2 and not any('Hidden' in label for label in labels)
        assert 'file present, create chat' in labels[0]
        if selection is None:
            assert not questions and not requested and not sent
            assert not metadata_requests
            assert len(frame.store.list_sessions(frame.owner_id)) == 1
        else:
            expected = entries[selection + 1]['url']
            assert requested == ([] if selection == 0 else [expected])
            assert frame.current.source_url == expected
            assert frame.current.initial_question == 'Describe'
            assert frame.current.transcript_state == 'pending'
            assert metadata_requests == ([expected] if selection == 0 else [])
            if selection == 0:
                assert frame.current.video_info['description'] == 'Full description'
                assert frame.current.video_info['tags'] == ['known product']
                assert frame.current.video_info['chapters'][0]['title'] == 'Introduction'
            assert sent == [frame.current.id]
            assert Path(frame.current.source_path) == (orphan if selection == 0 else downloads/'Missing [ccccccccccc].mp4')
        assert hidden.read_bytes() == b'hidden video' and orphan.read_bytes() == b'orphan video'
    finally:
        frame.Destroy()
        app.Destroy()
