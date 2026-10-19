"""Download ownership tests: no directory-difference or prefix based cleanup."""
import sys
import types

import pytest

from sceneaccess import youtube


@pytest.mark.parametrize('cancel', [False, True])
def test_gui_delegates_download_and_preserves_unrelated_files(tmp_path, monkeypatch, cancel):
    from sceneaccess import new_chat
    unrelated = tmp_path / 'other-download.part'
    pending = []
    controls = []
    labels = []

    class Thread:
        def __init__(self, target, **kwargs):
            pending.append(target)
        def start(self):
            pass
        def join(self):
            pass

    class Dialog:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def Update(self, value): return not cancel, False

    def download(url, folder, height, progress, fields, cookies, *, control):
        assert folder == tmp_path
        controls.append(control)
        unrelated.write_bytes(b'foreign')
        progress({'status': 'downloading', 'downloaded_bytes': 1, 'total_bytes': 100})
        raise RuntimeError('failed')

    monkeypatch.setattr(new_chat.threading, 'Thread', Thread)
    monkeypatch.setattr(new_chat.wx, 'GenericProgressDialog', Dialog)
    monkeypatch.setattr(new_chat.wx, 'MilliSleep', lambda ms: pending.pop()())
    monkeypatch.setattr(new_chat.wx, 'MessageBox', lambda *args: None)
    monkeypatch.setattr(new_chat, 'announce', lambda *args: None)
    monkeypatch.setattr(new_chat, 'download_video_isolated', download)
    host = types.SimpleNamespace(config=types.SimpleNamespace(filename_fields=(), youtube_cookies=''),
                                 status=types.SimpleNamespace(SetLabel=labels.append))
    assert new_chat.NewChatMixin._download_with_progress(host, 'url', tmp_path, 480) is None
    assert unrelated.read_bytes() == b'foreign'
    assert len(controls) == 1
    if cancel:
        assert 'HLS' in labels[-1] and str(tmp_path) in labels[-1]
        from sceneaccess.request_control import RequestCancelled
        with pytest.raises(RequestCancelled):
            controls[0].check_cancelled()


def test_failed_download_removes_only_its_registered_files(tmp_path, monkeypatch):
    existing = tmp_path / 'video.mp4'
    existing.write_bytes(b'original')
    unrelated = tmp_path / 'video.mp4.notes'

    class Downloader:
        def __init__(self, options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, url, download):
            self.dl(str(existing), {})
        def dl(self, name, info, subtitle=False, test=False):
            partial = tmp_path / 'video.mp4.part'
            partial.write_bytes(b'partial')
            unrelated.write_bytes(b'concurrent')
            for hook in self.options.get('progress_hooks', []):
                hook({'status': 'downloading', 'filename': name, 'tmpfilename': str(partial)})
            raise RuntimeError('network failed')

    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=Downloader))
    with pytest.raises(RuntimeError, match='network failed'):
        youtube.download_video('https://example.test/video', tmp_path)
    assert existing.read_bytes() == b'original'
    assert unrelated.read_bytes() == b'concurrent'
    assert not (tmp_path / 'video.mp4.part').exists()


def test_failed_merge_cleans_exact_final_and_temp_paths(tmp_path, monkeypatch):
    final = tmp_path / 'video.mp4'
    temp = tmp_path / 'video.temp.mp4'
    other = tmp_path / 'video.temp.mp4.notes'

    class Downloader:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            return self.run_pp(object(), {'filepath': str(final)})
        def run_pp(self, pp, info):
            temp.write_bytes(b'partial merge')
            other.write_bytes(b'foreign')
            raise RuntimeError('merge failed')

    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=Downloader))
    with pytest.raises(RuntimeError, match='merge failed'):
        youtube.download_video('url', tmp_path)
    assert not temp.exists()
    assert other.read_bytes() == b'foreign'


def test_returned_path_must_be_inside_target(tmp_path, monkeypatch):
    class Downloader:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            return {'requested_downloads': [{'filepath': str(tmp_path.parent / 'outside.mp4')}]}
    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=Downloader))
    with pytest.raises(ValueError, match='outside'):
        youtube.download_video('url', tmp_path)


@pytest.mark.parametrize('suffix', ['', '.part', '.ytdl', '.part.ytdl', '.temp.mp4'])
def test_existing_job_named_files_are_never_admitted(tmp_path, suffix):
    path = tmp_path / ('video.mp4' + suffix)
    path.write_bytes(b'original')
    files = youtube.DownloadFiles(tmp_path)
    files.reserve(path)
    files.observe()
    files.cleanup()
    assert path.read_bytes() == b'original'


def test_registering_after_creation_does_not_establish_ownership(tmp_path):
    files = youtube.DownloadFiles(tmp_path)
    foreign = tmp_path / 'foreign.part'
    foreign.write_bytes(b'foreign')
    files.reserve(foreign)
    files.observe()
    files.cleanup()
    assert foreign.read_bytes() == b'foreign'


def test_resumed_existing_partial_is_protected_after_rename(tmp_path):
    partial, final = tmp_path / 'video.mp4.part', tmp_path / 'video.mp4'
    partial.write_bytes(b'original')
    files = youtube.DownloadFiles(tmp_path)
    files.reserve(final)
    files.reserve(partial)
    partial.rename(final)
    files.observe()
    files.cleanup()
    assert final.read_bytes() == b'original'


def test_cleanup_spares_replacement_at_an_owned_path(tmp_path):
    path = tmp_path / 'video.part'
    files = youtube.DownloadFiles(tmp_path)
    files.reserve(path)
    path.write_bytes(b'own')
    files.observe()
    path.rename(tmp_path / 'moved')
    path.write_bytes(b'replacement')
    files.observe()
    files.cleanup()
    assert path.read_bytes() == b'replacement'


@pytest.mark.parametrize('kind', ['outside', 'symlink', 'subfolder'])
def test_ownership_rejects_unsafe_paths(tmp_path, kind):
    files = youtube.DownloadFiles(tmp_path)
    if kind == 'outside':
        path = tmp_path / '..' / 'outside.part'
    elif kind == 'symlink':
        target = tmp_path / 'target'
        target.write_bytes(b'foreign')
        path = tmp_path / 'link.part'
        path.symlink_to(target)
    else:
        (tmp_path / 'nested').mkdir()
        path = tmp_path / 'nested' / 'video.part'
    with pytest.raises(ValueError):
        files.reserve(path)


@pytest.mark.parametrize('cancel', [False, True])
def test_real_yt_dlp_download_stays_in_target_and_cleans_on_cancel(tmp_path, cancel):
    import http.server
    import threading
    from sceneaccess.new_chat import DownloadCancelled

    payload = b'video data' * 2000

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args): pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    seen = []
    foreign = tmp_path / 'unrelated.part'

    def progress(update):
        seen.append(update)
        foreign.write_bytes(b'foreign')
        if cancel:
            raise DownloadCancelled()

    try:
        url = f'http://127.0.0.1:{server.server_port}/video.mp4'
        if cancel:
            with pytest.raises(DownloadCancelled):
                youtube.download_video(url, tmp_path, progress=progress)
            assert list(tmp_path.iterdir()) == [foreign]
        else:
            path, title, info = youtube.download_video(url, tmp_path, progress=progress)
            assert path.parent == tmp_path
            assert path.read_bytes() == payload
        assert seen
        assert foreign.read_bytes() == b'foreign'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
