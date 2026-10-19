import sys
import threading
import time
import types

import pytest
from sceneaccess import youtube
from sceneaccess.request_control import RequestControl, RequestCancelled


_real_worker = youtube._download_video_isolated_worker


def _merge_worker(url,target_dir,max_height,filename_fields,cookies,_progress_pipe=None,_job_id=''):
    class Downloader:
        def __init__(self,options): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def extract_info(self,url,download):
            return self.run_pp(object(),{'filepath':str(target_dir/'job.mp4')})
        def run_pp(self,pp,info):
            partial = target_dir/'job.temp.mp4'
            partial.write_bytes(b'private partial merge')
            if url == 'rename': partial.replace(target_dir/'job.mp4')
            (target_dir.parent/'started').touch()
            time.sleep(20)
    sys.modules['yt_dlp'] = types.SimpleNamespace(YoutubeDL=Downloader)
    return _real_worker(url,target_dir,max_height,filename_fields,cookies,
                        _progress_pipe=_progress_pipe,_job_id=_job_id)


def test_real_ffmpeg_merge_accepts_preclaimed_private_output(tmp_path, monkeypatch):
    import subprocess
    import yt_dlp
    import imageio_ffmpeg
    from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    video, audio = tmp_path/'video.mp4', tmp_path/'audio.m4a'
    subprocess.run([executable,'-loglevel','error','-f','lavfi','-i','testsrc2=size=64x64:duration=0.3',
                    '-an','-c:v','libx264',str(video)],check=True)
    subprocess.run([executable,'-loglevel','error','-f','lavfi','-i','sine=duration=0.3',
                    '-vn','-c:a','aac',str(audio)],check=True)
    target = tmp_path/'downloads'
    final = target/'merged.mp4'
    original = yt_dlp.YoutubeDL
    class LocalMerge(original):
        def extract_info(self,url,download):
            return self.run_pp(FFmpegMergerPP(self), {
                'filepath':str(final), 'ext':'mp4', 'title':'Fixture', 'id':'fixture',
                'requested_formats':[
                    {'filepath':str(video),'vcodec':'h264','acodec':'none','protocol':'https'},
                    {'filepath':str(audio),'vcodec':'none','acodec':'aac','protocol':'https'}],
                '__files_to_merge':[str(video),str(audio)],
                'requested_downloads':[{'filepath':str(final)}]})
    monkeypatch.setattr(yt_dlp,'YoutubeDL',LocalMerge)
    result,_,_ = youtube.download_video('offline',target)
    assert result == final and final.stat().st_size > 0
    assert list(target.iterdir()) == [final]


@pytest.mark.parametrize('rename', [False,True])
def test_hard_cancel_removes_owned_merge_output_even_after_rename(tmp_path,monkeypatch,rename):
    target = tmp_path/'downloads'
    target.mkdir()
    foreign = target/'notes.txt'
    foreign.write_bytes(b'keep')
    monkeypatch.setattr(youtube,'_download_video_isolated_worker',_merge_worker)
    control = RequestControl()
    errors = []
    def run():
        try:
            youtube.download_video_isolated('rename' if rename else 'merge',target,control=control)
        except Exception as error: errors.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline=time.monotonic()+5
        while not (tmp_path/'started').exists() and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.01)
        assert (tmp_path/'started').exists(), str(errors)
        control.cancel()
        thread.join(3)
        assert not thread.is_alive()
        assert len(errors)==1 and isinstance(errors[0],RequestCancelled)
        assert list(target.iterdir()) == [foreign]
        assert foreign.read_bytes()==b'keep'
    finally:
        control.cancel()
        thread.join(3)
