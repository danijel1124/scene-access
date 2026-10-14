import subprocess

import imageio_ffmpeg
import pytest

from sceneaccess.tools import video_size_and_duration
from sceneaccess.video_size import (ShrinkCancelled, base64_size, megabytes, next_lower_height, shrink_plan,
                                    shrink_video)
from sceneaccess.youtube import MAX_HEIGHT_CHOICES


def make_video(path, seconds=6):
    # Noise keeps the bitrate high, so the file is clearly larger than the shrink target.
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=duration={seconds}:size=640x480:rate=25,noise=alls=60:allf=t", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "4M",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-y", str(path)], check=True)


def test_base64_size_and_megabytes():
    assert base64_size(0) == 0
    assert base64_size(3) == 4 and base64_size(4) == 8
    assert base64_size(46_040_000) == 61_386_668
    assert megabytes(19_000_000) == "19.0 MB"


def test_shrink_plan_saves_on_frames_before_picture_size_and_keeps_stereo():
    # An 11-minute video into 18 MB: little room, so 5 frames per second but still 360p.
    tight = shrink_plan(18_000_000, 692)
    assert tight.audio_kbps == 48 and tight.fps == 5 and tight.max_height == 360
    assert shrink_plan(18_000_000, 3000).max_height == 144
    roomy = shrink_plan(18_000_000, 60)
    assert roomy.max_height == 480 and roomy.audio_kbps == 96 and roomy.fps is None


def test_next_lower_height_always_gets_smaller():
    assert next_lower_height(480, 480, MAX_HEIGHT_CHOICES) == 360
    # The site had no 360p format and sent 480p again: the next step still goes below 360p.
    assert next_lower_height(360, 480, MAX_HEIGHT_CHOICES) == 240
    assert next_lower_height(480, 0, MAX_HEIGHT_CHOICES) == 360  # height unknown
    assert next_lower_height(240, 240, MAX_HEIGHT_CHOICES) is None


def test_shrink_video_fits_the_target_and_keeps_stereo_sound(tmp_path):
    source, target = tmp_path / "big.mp4", tmp_path / "small.mp4"
    make_video(source)
    goal = 1_000_000
    assert base64_size(source.stat().st_size) > goal
    fractions = []
    shrink_video(source, target, goal, fractions.append)
    assert base64_size(target.stat().st_size) <= goal
    assert fractions and fractions[-1] > 0.5
    (_width, height), duration = video_size_and_duration(target)
    assert height <= 480 and duration == pytest.approx(6, abs=0.5)
    probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(target)],
                           capture_output=True, text=True).stderr
    assert "stereo" in probe


def test_shrink_video_can_be_cancelled(tmp_path):
    source = tmp_path / "big.mp4"
    make_video(source, seconds=3)
    with pytest.raises(ShrinkCancelled):
        shrink_video(source, tmp_path / "small.mp4", 500_000, cancelled=lambda: True)
