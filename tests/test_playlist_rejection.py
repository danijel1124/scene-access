"""Offline integration checks: a chat accepts one video, not a collection."""
import pickle
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import imageio_ffmpeg
import pytest

from sceneaccess.youtube import download_video_isolated


@pytest.fixture
def video_server(tmp_path, request):
    ext = getattr(request, 'param', 'mp4')
    clip = tmp_path / ('fixture.' + ext)
    codec = 'libvpx-vp9' if ext == 'webm' else 'libx264'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=64x64:duration=0.3',
                    '-an', '-c:v', codec, str(clip)], check=True)
    payload = clip.read_bytes()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == '/index.html':
                body = (b'<html><head><title>Two videos</title></head><body>'
                        b'<video controls src="/one.mp4"></video>'
                        b'<video controls src="/two.mp4"></video></body></html>')
                content_type = 'text/html'
            elif self.path == '/single.html':
                body = (b'<html><head><title>One video</title></head><body>'
                        b'<video controls src="/one.mp4"></video></body></html>')
                content_type = 'text/html'
            else:
                body = payload
                content_type = {'mp4': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime'}[ext]
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_collection_is_rejected_before_downloading_any_video(tmp_path, video_server):
    url, requests, _payload = video_server
    target = tmp_path / 'downloads'
    with pytest.raises(RuntimeError, match='single video'):
        download_video_isolated(url + '/index.html', target)
    assert '/index.html' in requests
    assert '/one.mp4' not in requests and '/two.mp4' not in requests
    assert list(target.iterdir()) == []


def test_hd_collection_is_rejected_before_any_media_transfer(tmp_path, video_server):
    from sceneaccess.youtube import download_section
    url, requests, _payload = video_server
    target = tmp_path / 'hd'
    with pytest.raises(ValueError, match='single video'):
        download_section(url + '/index.html', 0, 0.15, target)
    assert '/index.html' in requests
    assert '/one.mp4' not in requests and '/two.mp4' not in requests
    assert not target.exists() or list(target.iterdir()) == []


def test_hd_single_video_still_produces_a_decodable_clip(tmp_path, video_server):
    from sceneaccess.youtube import download_section
    url, requests, _payload = video_server
    target = tmp_path / 'hd'
    path = download_section(url + '/one.mp4', 0, 0.15, target)
    assert path == target / 'section.mp4'
    assert path.stat().st_size > 0
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error',
                    '-i', str(path), '-f', 'null', '-'], check=True, capture_output=True)
    assert '/one.mp4' in requests and '/two.mp4' not in requests


def test_hd_tool_rejects_a_previously_stored_collection(tmp_path, video_server):
    from sceneaccess.session_store import VideoSessionStore
    from sceneaccess.tools import HD_FRAME, ToolRunner, tool_definitions
    from sceneaccess.request_control import RequestControl
    url, requests, payload = video_server
    source = tmp_path / 'fixture.mp4'
    source.write_bytes(payload)
    session = VideoSessionStore(tmp_path / 'data').create_session(
        owner_id='fixture', source_path=source, title='Legacy collection', model='fixture',
        initial_question='?', source_url=url + '/index.html', tools=tool_definitions(online_video=True))
    result = ToolRunner(session, control=RequestControl()).run(HD_FRAME, '{"time": 0}')
    assert result.text.startswith('Error:') and 'single video' in result.text
    assert result.image is None
    assert '/index.html' in requests
    assert '/one.mp4' not in requests and '/two.mp4' not in requests


def test_single_video_still_downloads_normally(tmp_path, video_server):
    url, requests, payload = video_server
    target = tmp_path / 'downloads'
    path, _title, _info = download_video_isolated(url + '/one.mp4', target)
    assert path.parent == target
    assert path.read_bytes() == payload
    assert '/one.mp4' in requests and '/two.mp4' not in requests


def test_collection_choices_are_metadata_only_and_selected_url_downloads(tmp_path, video_server):
    from sceneaccess.youtube import list_video_choices
    from sceneaccess.request_control import RequestControl
    from sceneaccess.worker_process import run_isolated_task

    url, requests, payload = video_server
    result = run_isolated_task(list_video_choices, (url + '/index.html',), control=RequestControl())
    assert result['is_playlist'] is True
    assert result['title'] == 'Two videos'
    assert [entry['url'] for entry in result['entries']] == [url + '/one.mp4', url + '/two.mp4']
    assert all(set(entry) == {'id', 'url', 'title', 'info', 'filename', 'filenames'} for entry in result['entries'])
    assert all(isinstance(entry['id'], str) and isinstance(entry['info'], dict) for entry in result['entries'])
    # Generic direct media URLs need metadata/header inspection for standalone
    # naming; skip_download prevents creating media files during that inspection.
    assert requests[0] == '/index.html'
    before_one = requests.count('/one.mp4')
    assert pickle.loads(pickle.dumps(result)) == result
    path, _title, _info = download_video_isolated(result['entries'][1]['url'], tmp_path / 'selected')
    assert path.read_bytes() == payload
    assert '/two.mp4' in requests and requests.count('/one.mp4') == before_one


def test_collection_reopening_finds_completed_single_download(tmp_path, video_server, monkeypatch):
    from sceneaccess.youtube import list_video_choices, download_video
    from sceneaccess.playlist_files import playlist_options
    url, _requests, _payload = video_server
    import yt_dlp
    with monkeypatch.context() as patch:
        patch.setattr(yt_dlp.YoutubeDL, 'dl', lambda *a, **kw: pytest.fail('metadata lookup started a media downloader'))
        listing = list_video_choices(url + '/index.html')
    entry = listing['entries'][0]
    path, _, _ = download_video(entry['url'], tmp_path/'downloads', _job_id='a'*32)
    fresh = list_video_choices(url + '/index.html')
    assert playlist_options(fresh['entries'], path.parent, [])[0]['local_path'] == path


def test_single_page_returns_one_choice_without_fetching_media(video_server):
    from sceneaccess.youtube import list_video_choices

    url, requests, _payload = video_server
    result = list_video_choices(url + '/single.html')
    assert result['is_playlist'] is False
    assert result['title'] == 'One video (1)'
    assert len(result['entries']) == 1
    assert result['entries'][0]['url'] == url + '/single.html'
    assert requests == ['/single.html']


@pytest.fixture
def extractor_seam(monkeypatch):
    """Replace extraction only; retain real yt-dlp processing and downloader paths."""
    from yt_dlp.extractor.generic import GenericIE

    original = GenericIE._real_extract
    results = {}
    calls = []

    def extract(extractor, url):
        if url not in results:
            return original(extractor, url)
        calls.append(url)
        return results[url]()

    monkeypatch.setattr(GenericIE, '_real_extract', extract)
    return results, calls


@pytest.mark.parametrize('kind', ['playlist', 'multi_video'])
def test_extractor_collections_are_flat_and_lazy(video_server, extractor_seam, kind):
    from sceneaccess.youtube import list_video_choices

    url, requests, _payload = video_server
    results, calls = extractor_seam
    collection_url = url + '/seam'
    consumed = []

    def entries():
        consumed.append(True)
        yield {'_type': 'url', 'id': 'first', 'url': url + '/one.mp4', 'title': 'First'}
        yield None
        yield {'_type': 'url', 'id': 'second', 'url': url + '/two.mp4', 'title': 'Second'}

    results[collection_url] = lambda: {'_type': kind, 'id': 'collection', 'title': 'Collection',
                                     'entries': entries()}
    result = list_video_choices(collection_url)
    assert result['is_playlist'] is True
    assert result['title'] == 'Collection'
    assert [entry['id'] for entry in result['entries']] == ['first', 'second']
    assert [entry['url'] for entry in result['entries']] == [url + '/one.mp4', url + '/two.mp4']
    assert pickle.loads(pickle.dumps(result)) == result
    assert consumed == [True]
    assert calls == [collection_url]
    assert requests == []


@pytest.mark.parametrize('kind', ['playlist', 'multi_video'])
def test_extractor_collection_guard_never_consumes_children(tmp_path, video_server, extractor_seam, kind):
    from sceneaccess.youtube import download_video

    url, requests, _payload = video_server
    results, calls = extractor_seam
    consumed = []

    def entries():
        consumed.append(True)
        yield {'id': 'first', 'title': 'First', 'url': url + '/one.mp4', 'ext': 'mp4'}

    results[url + '/seam'] = lambda: {'_type': kind, 'id': 'collection', 'entries': entries()}
    target = tmp_path / 'guarded'
    with pytest.raises(ValueError, match='single video'):
        download_video(url + '/seam', target)
    assert consumed == []
    assert calls == [url + '/seam']
    assert requests == []
    assert list(target.iterdir()) == []


def test_nested_collections_are_not_recursively_expanded(video_server, extractor_seam):
    from sceneaccess.youtube import list_video_choices

    url, requests, _payload = video_server
    results, _calls = extractor_seam
    nested_consumed = []

    def nested_entries():
        nested_consumed.append(True)
        yield {'_type': 'url', 'url': url + '/two.mp4'}

    results[url + '/seam'] = lambda: {
        '_type': 'playlist', 'id': 'outer', 'entries': [
            {'_type': 'url', 'id': 'one', 'url': url + '/one.mp4'},
            {'_type': 'playlist', 'id': 'inner', 'extractor': 'generic',
             'extractor_key': 'Generic', 'entries': nested_entries()},
        ]}
    result = list_video_choices(url + '/seam')
    assert [entry['id'] for entry in result['entries']] == ['one']
    assert nested_consumed == []
    assert requests == ['/one.mp4']  # Only standalone metadata of the available child, never the nested collection.


def test_missing_and_unavailable_entries_are_skipped(video_server, extractor_seam):
    from sceneaccess.youtube import list_video_choices

    url, requests, _payload = video_server
    results, _calls = extractor_seam
    results[url + '/seam'] = lambda: {
        '_type': 'playlist', 'id': 'outer', 'entries': [
            None,
            {'_type': 'url', 'id': 'missing'},
            {'_type': 'url', 'id': 'bad', 'url': 'file:///etc/passwd'},
            {'_type': 'url', 'id': 'private', 'url': url + '/private', 'availability': 'private'},
            {'_type': 'url', 'id': 'ok', 'url': url + '/one.mp4', 'title': 'Available'},
        ]}
    result = list_video_choices(url + '/seam')
    assert [entry['id'] for entry in result['entries']] == ['ok']
    assert requests == []


def test_watch_with_playlist_keeps_single_video_and_cookie_options(tmp_path, video_server, monkeypatch):
    from sceneaccess.youtube import list_video_choices
    from yt_dlp.extractor.youtube import YoutubeIE

    url, requests, _payload = video_server
    cookies = tmp_path / 'cookies.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
    calls = []
    watch = 'https://www.youtube.com/watch?v=abcdefghijk&list=PLfixture'

    def extract(extractor, source_url):
        calls.append(source_url)
        assert extractor._downloader.params['cookiefile'] == str(cookies)
        assert extractor._downloader.params['skip_download'] is True
        assert extractor._downloader.params['extract_flat'] == 'in_playlist'
        # Exercise yt-dlp's real playlist-versus-watch disambiguation.
        assert extractor._yes_playlist('PLfixture', 'abcdefghijk') is False
        return {'id': 'abcdefghijk', 'title': 'Single watch', 'url': url + '/one.mp4', 'ext': 'mp4'}

    monkeypatch.setattr(YoutubeIE, '_real_extract', extract)
    result = list_video_choices(watch, cookies=str(cookies))
    assert result['is_playlist'] is False
    assert result['title'] == 'Single watch'
    assert len(result['entries']) == 1
    assert result['entries'][0]['id'] == 'abcdefghijk'
    assert result['entries'][0]['url'] == 'https://www.youtube.com/watch?v=abcdefghijk'
    assert len(calls) == 1
    assert calls[0] in (watch, 'https://www.youtube.com/watch?v=abcdefghijk')
    assert requests == []


@pytest.mark.parametrize('failed_index', [0, 1, 2])
def test_failed_entry_metadata_preserves_other_choices(video_server, extractor_seam, failed_index):
    from sceneaccess.youtube import list_video_choices
    from yt_dlp.utils import ExtractorError

    url, requests, _payload = video_server
    results, calls = extractor_seam
    children = [url + '/first', url + '/removed', url + '/last']
    failed = children.pop(1)
    children.insert(failed_index, failed)
    results[url + '/seam'] = lambda: {
        '_type': 'playlist', 'id': 'collection', 'entries': [
            {'_type': 'url', 'id': child.rsplit('/', 1)[-1], 'title': child.rsplit('/', 1)[-1],
             'url': child} for child in children]}

    def removed():
        raise ExtractorError('This video has been removed', expected=True)

    results[failed] = removed
    for child in children:
        if child != failed:
            results[child] = lambda child=child: {
                'id': child.rsplit('/', 1)[-1], 'title': child.rsplit('/', 1)[-1],
                'uploader': 'Known', 'url': url + '/one.mp4', 'ext': 'mp4'}
    listing = list_video_choices(url + '/seam', filename_fields=('title', 'uploader'))
    assert [entry['id'] for entry in listing['entries']] == ['first', 'last']
    assert calls == [url + '/seam', *children]
    assert requests == []


@pytest.mark.parametrize('scope', ['collection', 'child'])
def test_metadata_cancellation_propagates(video_server, extractor_seam, scope):
    from sceneaccess.youtube import list_video_choices
    from sceneaccess.request_control import RequestCancelled

    url, requests, _payload = video_server
    results, _calls = extractor_seam

    def cancelled():
        raise RequestCancelled('cancelled metadata')

    results[url + '/seam'] = lambda: {
        '_type': 'playlist', 'id': 'collection', 'entries': [
            {'_type': 'url', 'id': 'child', 'title': 'Child', 'url': url + '/child'}]}
    results[url + '/seam' if scope == 'collection' else url + '/child'] = cancelled
    with pytest.raises(RequestCancelled, match='cancelled metadata'):
        list_video_choices(url + '/seam', filename_fields=('title', 'uploader'))
    assert requests == []


def test_root_collection_error_propagates(video_server, extractor_seam):
    from sceneaccess.youtube import list_video_choices
    from yt_dlp.utils import DownloadError, ExtractorError

    url, requests, _payload = video_server
    results, _calls = extractor_seam

    def unavailable():
        raise ExtractorError('Collection requires sign in', expected=True)

    results[url + '/seam'] = unavailable
    with pytest.raises(DownloadError, match='requires sign in'):
        list_video_choices(url + '/seam')
    assert requests == []


@pytest.mark.parametrize('ext', ['mp4', 'webm', 'mov'])
def test_known_extension_does_not_offer_other_container_names(video_server, extractor_seam, ext):
    from sceneaccess.youtube import list_video_choices

    url, requests, _payload = video_server
    results, calls = extractor_seam
    results[url + '/seam'] = lambda: {
        '_type': 'playlist', 'id': 'collection', 'entries': [
            {'_type': 'url', 'id': 'child', 'title': 'Child',
             'url': url + '/child', 'ext': ext}]}
    entry = list_video_choices(url + '/seam')['entries'][0]
    assert entry['filename'] == 'Child [child].' + ext
    assert entry['filenames'] == [entry['filename']]
    assert calls == [url + '/seam']
    assert requests == []


@pytest.mark.parametrize('fields, expected', [(('title', 'id'), 'First [first].mp4'),
                                             (('title',), 'First.mp4')])
def test_flat_choices_predict_expected_basename(video_server, extractor_seam, fields, expected):
    from sceneaccess.youtube import list_video_choices

    url, requests, _payload = video_server
    results, _calls = extractor_seam
    results[url + '/seam'] = lambda: {
        '_type': 'playlist', 'id': 'collection', 'entries': [
            {'_type': 'url', 'id': 'first', 'title': 'First', 'url': url + '/one.mp4'},
        ]}
    result = list_video_choices(url + '/seam', filename_fields=fields, max_height=360)
    assert result['entries'][0]['filename'] == expected
    assert requests == []


@pytest.mark.parametrize('video_server', ['mp4', 'webm', 'mov'], indirect=True)
@pytest.mark.parametrize('job_id', ['', 'a' * 32])
@pytest.mark.parametrize('title_length', [40, 220])
def test_flat_unknown_extension_reuses_real_container_download(
        tmp_path, video_server, extractor_seam, request, job_id, title_length):
    from sceneaccess.youtube import download_video, list_video_choices
    from sceneaccess.playlist_files import playlist_options

    url, requests, payload = video_server
    ext = request.node.callspec.params['video_server']
    results, calls = extractor_seam
    source = url + '/video'
    title = 'V' * title_length
    results[url + '/seam'] = lambda: {
        '_type': 'playlist', 'id': 'collection', 'entries': [
            {'_type': 'url', 'id': 'fixture', 'title': title, 'url': source}]}
    results[source] = lambda: {
        'id': 'fixture', 'title': title, 'url': url + '/media.' + ext, 'ext': ext}
    fields = ('title',)
    listing = list_video_choices(url + '/seam', filename_fields=fields)
    assert calls == [url + '/seam']  # No per-entry extraction just to discover ext.
    assert requests == []
    path, _, _ = download_video(source, tmp_path / 'downloads',
                                filename_fields=fields, _job_id=job_id)
    assert path.suffix == '.' + ext
    assert path.read_bytes() == payload
    fresh = list_video_choices(url + '/seam', filename_fields=fields)
    assert playlist_options(fresh['entries'], path.parent, [])[0]['local_path'] == path


@pytest.mark.parametrize('title_length', [40, 220])
def test_job_prefixed_download_is_found_by_predicted_name(tmp_path, video_server, extractor_seam, title_length):
    from sceneaccess.youtube import download_video, list_video_choices
    from sceneaccess.playlist_files import playlist_options
    url, _requests, _payload = video_server
    results, _calls = extractor_seam
    source = url + '/long-title'
    results[source] = lambda: {'id': 'fixture', 'title': 'V' * title_length,
                               'url': url + '/one.mp4', 'ext': 'mp4'}
    fields = ('title',)
    entry = list_video_choices(source, filename_fields=fields)['entries'][0]
    folder = tmp_path/'downloads'
    path, _, _ = download_video(source, folder, filename_fields=fields, _job_id='a'*32)
    assert playlist_options([entry], folder, [])[0]['local_path'] == path


@pytest.mark.parametrize('fields', [('title', 'id'), ('title',)])
def test_predicted_single_filename_matches_real_download(tmp_path, video_server, fields):
    from sceneaccess.youtube import download_video, list_video_choices

    url, requests, payload = video_server
    result = list_video_choices(url + '/single.html', filename_fields=fields)
    assert requests == ['/single.html']
    choice = result['entries'][0]
    path, _title, _info = download_video(choice['url'], tmp_path / 'named', filename_fields=fields)
    assert path.name == choice['filename']
    assert path.read_bytes() == payload
