import os
import stat
import subprocess
from pathlib import Path

import pytest
from sceneaccess import transcript
from sceneaccess.config import AppConfig, save_config


def test_partial_extraction_failure_is_not_accepted(tmp_path, monkeypatch):
    def fail(arguments, **kwargs):
        (Path(arguments[-1]).parent/'audio_000.mp3').write_bytes(b'partial')
        return subprocess.CompletedProcess([], 1, '', 'No space left on device')
    monkeypatch.setattr(transcript, 'run_ffmpeg', fail)
    with pytest.raises(RuntimeError, match='No space left'):
        transcript.extract_audio_chunks(tmp_path/'video.mp4',tmp_path)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission semantics')
def test_config_is_private_before_first_secret_byte(tmp_path, monkeypatch):
    root = tmp_path/'legacy'
    root.mkdir(mode=0o755)
    os.chmod(root,0o755)
    observed = []
    write_text, fdopen = Path.write_text, os.fdopen
    def observe_path(path, text, *args, **kwargs):
        result = write_text(path,text,*args,**kwargs)
        if path.parent == root:
            observed.append(stat.S_IMODE(path.stat().st_mode))
        return result
    def observe_fd(fd,*args,**kwargs):
        observed.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return fdopen(fd,*args,**kwargs)
    monkeypatch.setattr(Path, 'write_text', observe_path)
    monkeypatch.setattr(os, 'fdopen', observe_fd)
    previous = os.umask(0o022)
    try:
        save_config(root/'config.json',AppConfig(api_key='fixture-only-not-a-secret'))
        assert observed and all(mode & 0o077 == 0 for mode in observed)
        assert stat.S_IMODE((root/'config.json').stat().st_mode) == 0o600
        assert [p.name for p in root.iterdir()] == ['config.json']
    finally:
        os.umask(previous)
