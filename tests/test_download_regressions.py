"""Regression coverage for quality changes and uncooperative HTTP downloads."""
import http.server
import threading
import time
import types
from pathlib import Path

import pytest

from sceneaccess import new_chat, youtube


def test_quality_change_keeps_previous_file_until_replacement_succeeds(tmp_path, monkeypatch):
    original = tmp_path / 'existing.mp4'
    original.write_bytes(b'previous download or user file')
    calls = []

    class Entry:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ShowModal(self): return new_chat.wx.ID_OK
        def GetValue(self): return 'https://example.test/video'

    def download(url, folder, height):
        calls.append(height)
        if len(calls) == 1:
            return original, 'Video', {}
        assert original.read_bytes() == b'previous download or user file'
        return None

    host = types.SimpleNamespace(
        config=types.SimpleNamespace(youtube_max_height=480),
        _ask_first_question=lambda **kwargs: ('Describe', ''),
        _download_dir=lambda: tmp_path,
        _download_with_progress=download,
        _video_to_send=lambda *args, **kwargs: new_chat.SIZE_LOWER,
    )
    monkeypatch.setattr(new_chat.wx, 'TextEntryDialog', Entry)
    monkeypatch.setattr(new_chat, 'url_from_clipboard', lambda: '')
    monkeypatch.setattr(new_chat, 'video_height', lambda path: 480)
    new_chat.NewChatMixin.new_from_youtube(host)
    assert calls == [480, 360]
    assert original.exists()


@pytest.mark.parametrize('name', ['video', 'v'*190])
def test_isolated_download_never_reuses_existing_quality(tmp_path, monkeypatch, name):
    from sceneaccess.request_control import RequestControl
    payload = [b'high quality']
    requests = []
    monkeypatch.chdir(tmp_path)
    target = Path('downloads')
    target.mkdir()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = payload[0]
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # This is the default filename yt-dlp would otherwise silently reuse.
    existing = target / 'video [video].mp4'
    existing.write_bytes(b'pre-existing user file')
    try:
        url = f'http://127.0.0.1:{server.server_port}/{name}.mp4'
        high, _, _ = youtube.download_video_isolated(url, target, 480, control=RequestControl())
        before = len(requests)
        payload[0] = b'low quality'
        low, _, _ = youtube.download_video_isolated(url, target, 240, control=RequestControl())
        assert len(requests) > before
        assert high != low
        assert high.read_bytes() == b'high quality'
        assert low.read_bytes() == b'low quality'
        assert existing.read_bytes() == b'pre-existing user file'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_isolated_cancel_cleans_registered_files_but_keeps_foreign_files(tmp_path):
    from sceneaccess.request_control import RequestControl, RequestCancelled
    control = RequestControl()
    foreign = tmp_path/'foreign.part'
    existing = tmp_path/'existing.mp4'
    existing.write_bytes(b'original')
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(1024*1024))
            self.end_headers()
            try:
                for _ in range(256):
                    self.wfile.write(b'x'*4096)
                    self.wfile.flush()
                    time.sleep(.005)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args): pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def progress(update):
        if update.get('downloaded_bytes', 0):
            foreign.write_bytes(b'unrelated')
            control.cancel()
    try:
        with pytest.raises(RequestCancelled):
            youtube.download_video_isolated(f'http://127.0.0.1:{server.server_port}/file.mp4',
                                             tmp_path, progress=progress, control=control)
        assert existing.read_bytes() == b'original'
        assert foreign.read_bytes() == b'unrelated'
        assert set(tmp_path.iterdir()) == {existing, foreign}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_gui_cancel_interrupts_http_header_stall(tmp_path, monkeypatch):
    received, release = threading.Event(), threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            received.set()
            release.wait(5)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args): pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class Dialog:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def Update(self, value): return not received.is_set(), False

    monkeypatch.setattr(new_chat.wx, 'GenericProgressDialog', Dialog)
    monkeypatch.setattr(new_chat.wx, 'MilliSleep', lambda ms: time.sleep(.02))
    monkeypatch.setattr(new_chat, 'announce', lambda *args: None)
    monkeypatch.setattr(new_chat.wx, 'MessageBox', lambda *args: pytest.fail('Cancellation is not an error'))
    host = types.SimpleNamespace(config=types.SimpleNamespace(filename_fields=(), youtube_cookies=''),
                                 status=types.SimpleNamespace(SetLabel=lambda text: None))
    start = time.monotonic()
    try:
        result = new_chat.NewChatMixin._download_with_progress(
            host, f'http://127.0.0.1:{server.server_port}/stall.mp4', tmp_path, 480)
        elapsed = time.monotonic() - start
        assert received.is_set()
        assert result is None
        assert elapsed < 3, f'Cancellation took {elapsed:.2f}s'
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()
