"""HD downloads must find the bundled ffmpeg without a system installation."""
from contextvars import ContextVar
from pathlib import Path
import subprocess

import imageio_ffmpeg
import pytest
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor

from sceneaccess.youtube import download_section
from test_playlist_rejection import video_server


@pytest.fixture
def ffmpeg_context(monkeypatch):
    context = ContextVar('test_hd_ffmpeg_location', default=None)
    monkeypatch.setattr(FFmpegPostProcessor, '_ffmpeg_location', context)
    # Otherwise a successful system-ffmpeg probe from an earlier test can mask
    # the missing PATH executable. Restore the original caches after the test.
    monkeypatch.setattr(FFmpegPostProcessor, '_version_cache', {None: None})
    monkeypatch.setattr(FFmpegPostProcessor, '_features_cache', {})
    monkeypatch.setenv('PATH', '')
    return context


def test_hd_download_uses_bundled_ffmpeg_without_system_ffmpeg(tmp_path, video_server, ffmpeg_context):
    url, requests, _payload = video_server
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    assert Path(executable).is_file()
    assert ffmpeg_context.get() is None
    clip = download_section(url + '/one.mp4', 0, 0.15, tmp_path / 'hd')
    assert clip.is_file() and clip.stat().st_size > 0
    subprocess.run([executable, '-loglevel', 'error', '-i', str(clip), '-f', 'null', '-'],
                   check=True, capture_output=True)
    assert '/one.mp4' in requests
    assert ffmpeg_context.get() is None


def test_hd_failure_restores_the_callers_ffmpeg_context(tmp_path, video_server, ffmpeg_context):
    url, _requests, _payload = video_server
    token = ffmpeg_context.set('outer-call-ffmpeg')
    try:
        with pytest.raises(ValueError, match='single video'):
            download_section(url + '/index.html', 0, 0.15, tmp_path / 'hd')
        assert ffmpeg_context.get() == 'outer-call-ffmpeg'
    finally:
        ffmpeg_context.reset(token)
