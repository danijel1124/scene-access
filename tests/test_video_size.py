import subprocess
import sys
import threading
import time

import sceneaccess.video_size as video_size

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


@pytest.mark.parametrize("width,height", [(322, 181), (323, 180), (181, 322)])
def test_shrink_video_rounds_both_dimensions_down_without_enlarging(tmp_path, width, height):
    source, target = tmp_path / "odd.mov", tmp_path / "even.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=duration=1:size={width}x{height}:rate=5", "-c:v", "png", "-y", str(source)],
                   check=True)
    assert video_size_and_duration(source)[0] == (width, height)

    shrink_video(source, target, 1_000_000)

    (out_width, out_height), duration = video_size_and_duration(target)
    assert (out_width, out_height) == (width // 2 * 2, height // 2 * 2)
    assert 0 < out_width <= width and 0 < out_height <= height
    assert duration == pytest.approx(1, abs=0.25)
    assert base64_size(target.stat().st_size) <= 1_000_000


@pytest.mark.parametrize("sizes,attempts", [((2_000_000, 600_000), 2), ((2_000_000, 1_500_000, 1_200_000), 3)])
def test_shrink_video_aims_lower_after_an_overshoot_and_gives_up_after_three_attempts(tmp_path, monkeypatch,
                                                                                       sizes, attempts):
    from sceneaccess import tools
    goals, written = [], list(sizes)
    real_plan = video_size.shrink_plan
    monkeypatch.setattr(video_size, "shrink_plan", lambda goal, duration: goals.append(goal) or real_plan(goal, duration))
    monkeypatch.setattr(tools, "video_size_and_duration", lambda path: ((640, 360), 60.0))
    # The encoder overshoots: each attempt writes a file of the next given size.
    monkeypatch.setattr(video_size, "_run", lambda _ffmpeg, arguments, *_: video_size.Path(arguments[-1]).write_bytes(
        b"x" * written.pop(0)))
    target = tmp_path / "small.mp4"
    goal = 1_000_000
    if attempts == 3:
        with pytest.raises(RuntimeError, match="could not shrink"):
            video_size.shrink_video(tmp_path / "big.mp4", target, goal)
    else:
        video_size.shrink_video(tmp_path / "big.mp4", target, goal)
        assert base64_size(target.stat().st_size) <= goal
    assert len(goals) == attempts and goals[0] == goal
    assert all(later < earlier for earlier, later in zip(goals, goals[1:]))


def test_run_rejects_initial_cancellation_without_starting_child(tmp_path, monkeypatch):
    started = tmp_path / "started"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(started)!r}).touch()"]
    # Replace only command selection; Popen and all pipe/process handling stay real.
    monkeypatch.setattr(video_size, "_ffmpeg_command", lambda *_: command, raising=False)
    with pytest.raises(ShrinkCancelled):
        video_size._run(imageio_ffmpeg.get_ffmpeg_exe(), [], 1, lambda _: None, lambda: True)
    assert not started.exists()


def test_run_cancels_a_running_silent_child_and_stops_it(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    # A silent child that proves it is alive by growing a file, without any ffmpeg progress output.
    command = [sys.executable, "-c",
               "import time\n"
               f"with open({str(heartbeat)!r}, 'ab', buffering=0) as beat:\n"
               "    while True:\n"
               "        beat.write(b'.')\n"
               "        time.sleep(0.02)\n"]
    cancel = threading.Event()
    errors = []

    def work():
        try:
            video_size._run("unused", [], 1, lambda _: None, cancel.is_set)
        except Exception as error:
            errors.append(error)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(video_size, "_ffmpeg_command", lambda *_: command)
        thread = threading.Thread(target=work)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not (heartbeat.exists() and heartbeat.stat().st_size > 2) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert heartbeat.exists() and heartbeat.stat().st_size > 2, "the child never started"
            cancel.set()
            thread.join(3)
            assert not thread.is_alive()
        finally:
            cancel.set()
            thread.join(3)
    assert len(errors) == 1 and isinstance(errors[0], ShrinkCancelled)
    # A child that was only abandoned, not stopped, would keep growing the file.
    size = heartbeat.stat().st_size
    time.sleep(0.3)
    assert heartbeat.stat().st_size == size


def test_shrink_video_can_be_cancelled(tmp_path):
    source = tmp_path / "big.mp4"
    make_video(source, seconds=3)
    with pytest.raises(ShrinkCancelled):
        shrink_video(source, tmp_path / "small.mp4", 500_000, cancelled=lambda: True)
