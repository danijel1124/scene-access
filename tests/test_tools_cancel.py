import pytest

from sceneaccess import tools, youtube
from sceneaccess.request_control import RequestControl, RequestCancelled
from sceneaccess.session_store import VideoSessionStore


def test_cancelled_hd_download_never_starts_metadata_probe(tmp_path, monkeypatch):
    source = tmp_path/'fixture.mp4'
    source.write_bytes(b'fixture')
    session = VideoSessionStore(tmp_path/'data').create_session(owner_id='o', source_path=source, title='T', model='m',
        initial_question='?', tools=tools.tool_definitions(online_video=True), source_url='https://example.com/video')
    control, probes = RequestControl(), []
    def download(*args, **kwargs):
        control.cancel()
        return source
    # Simulate cancellation arriving just as the isolated downloader returns.
    monkeypatch.setattr(tools, 'run_isolated_task', lambda *args, **kwargs: download(), raising=False)
    monkeypatch.setattr(youtube, 'download_section', download)
    monkeypatch.setattr(tools, 'video_size_and_duration', lambda path: probes.append(path) or ((320,240), 1))
    with pytest.raises(RequestCancelled):
        tools.ToolRunner(session, control=control).run(tools.HD_FRAME, '{"time": 0}')
    assert probes == []


def test_controlled_zoom_extracts_real_media_in_worker(tmp_path):
    import subprocess
    import imageio_ffmpeg
    source = tmp_path/'fixture.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error', '-f', 'lavfi', '-i',
        'testsrc=duration=1:size=320x240:rate=10', '-pix_fmt', 'yuv420p', '-y', str(source)], check=True)
    session = VideoSessionStore(tmp_path/'data').create_session(owner_id='o', source_path=source, title='T', model='m',
        initial_question='?', tools=tools.tool_definitions(online_video=False))
    result = tools.ToolRunner(session, control=RequestControl()).run(tools.ZOOM_FRAME, '{"time": 0}')
    assert result.image and (session.directory/result.image).stat().st_size > 0
