"""Downloader-local counters and extraction times cannot identify orphaned videos."""
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import imageio_ffmpeg
import pytest
import yt_dlp

from sceneaccess.playlist_files import playlist_options
from sceneaccess.youtube import download_video, list_video_choices


@pytest.fixture
def counter_playlist(tmp_path, monkeypatch):
    payloads = {}
    for name, color in [('first', 'red'), ('second', 'blue')]:
        clip = tmp_path / (name + '.mp4')
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error',
                        '-f', 'lavfi', '-i', f'color=c={color}:size=64x64:duration=0.3',
                        '-an', '-c:v', 'libx264', str(clip)], check=True)
        payloads['/' + name + '.mp4'] = clip.read_bytes()
    assert payloads['/first.mp4'] != payloads['/second.mp4']

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = payloads.get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    extract = yt_dlp.YoutubeDL.extract_info

    def playlist_extract(self, url, *args, **kwargs):
        if url == base + '/playlist':
            return {'_type': 'playlist', 'id': 'fixture', 'title': 'Fixture',
                    'webpage_url': url, 'entries': [
                        {'id': name, 'title': name, 'ext': 'mp4',
                         'url': base + '/' + name + '.mp4',
                         'webpage_url': base + '/' + name + '.mp4'}
                        for name in ('first', 'second')]}
        return extract(self, url, *args, **kwargs)

    monkeypatch.setattr(yt_dlp.YoutubeDL, 'extract_info', playlist_extract)
    try:
        yield base, payloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.parametrize('fields', [('video_autonumber',), ('autonumber',),
                                   ('title', 'autonumber'), ('title', 'video_autonumber'),
                                   ('title', 'id', 'autonumber'),
                                   ('autonumber', 'video_autonumber'),
                                   ('epoch',), ('title', 'epoch')])
def test_run_dependent_names_never_identify_an_orphan(tmp_path, counter_playlist, fields):
    base, payloads = counter_playlist
    folder = tmp_path / 'downloads'
    second_url = base + '/second.mp4'
    path, _, _ = download_video(second_url, folder, filename_fields=fields, _job_id='a' * 32)
    assert path.read_bytes() == payloads['/second.mp4']
    listing = list_video_choices(base + '/playlist', filename_fields=fields)
    offered = playlist_options(listing['entries'], folder, [])
    assert len(offered) == 2
    assert all(entry['local_path'] is None for entry in offered)
    # Do not advertise counter-dependent guesses as usable filename candidates.
    assert all(entry['filenames'] == [] and entry['filename'] == '' for entry in listing['entries'])
    # An explicit saved source identity still supports hiding or reusing the file.
    chats = [{'source_url': second_url, 'source_path': str(path)}]
    visible = playlist_options(listing['entries'], folder, chats)
    assert [entry['url'] for entry in visible] == [base + '/first.mp4']
    reused = playlist_options(listing['entries'], folder, chats, hide_existing_chats=False)
    assert next(entry for entry in reused if entry['url'] == second_url)['local_path'] == path
