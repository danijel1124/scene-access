import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from sceneaccess.session_store import VideoSessionStore
from sceneaccess.tools import (HD_FRAME, VIDEO_INFO, ZOOM_FRAME, ToolRunner, describe_call, format_time,
                               function_tool_names, parse_time, tool_definitions)


def test_time_parsing_and_formatting():
    assert parse_time("02:51.5") == 171.5
    assert parse_time("1:02:03") == 3723
    assert parse_time(12) == 12
    with pytest.raises(ValueError):
        parse_time("-1")
    assert format_time(171.5) == "02:51.5"
    assert format_time(3723) == "1:02:03"


def test_online_tools_are_offered_only_for_online_videos_in_a_fixed_order():
    assert function_tool_names(tool_definitions(online_video=False)) == [ZOOM_FRAME]
    assert function_tool_names(tool_definitions(online_video=True)) == [ZOOM_FRAME, HD_FRAME, VIDEO_INFO]
    assert tool_definitions(online_video=True)[-1]["type"] == "openrouter:web_search"
    assert tool_definitions(online_video=True) == tool_definitions(online_video=True)


def test_tool_calls_are_described_readably():
    assert describe_call(ZOOM_FRAME, '{"time": "02:51", "x": 0.4, "y": 0.5, "width": 0.2, "height": 0.1}') == \
        "Zoom into 02:51, region x 0.4, y 0.5, width 0.2, height 0.1"
    assert describe_call(HD_FRAME, '{"time": 5}') == "Load in high resolution 00:05"
    assert describe_call(VIDEO_INFO, "{}") == "Read the video information"


def _video(path: Path) -> Path:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=2:size=320x240:rate=10", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path


def test_zoom_frame_saves_an_image_and_still_works_without_the_original(tmp_path: Path):
    source = _video(tmp_path / "test.mp4")
    store = VideoSessionStore(tmp_path / "data")
    session = store.create_session(owner_id="o", source_path=source, title="T", model="m", initial_question="?",
                                   tools=tool_definitions(online_video=False))
    runner = ToolRunner(session)

    result = runner.run(ZOOM_FRAME, '{"time": "00:01", "x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5}')

    assert result.image and (session.directory / result.image).stat().st_size > 0
    assert "160x120 source pixels of a 320x240 frame" in result.text
    source.unlink()
    assert ToolRunner(store.get_session(session.id, "o")).run(ZOOM_FRAME, '{"time": 1}').image


def test_tool_errors_are_returned_to_the_model_as_text(tmp_path: Path):
    source = _video(tmp_path / "test.mp4")
    session = VideoSessionStore(tmp_path / "data").create_session(
        owner_id="o", source_path=source, title="T", model="m", initial_question="?", tools=tool_definitions(online_video=False))
    runner = ToolRunner(session)

    assert runner.run(ZOOM_FRAME, '{"time": "00:30"}').text.startswith("Error: 00:30 is after the end")
    # Offline chats do not get the online tools, even if a model calls them anyway.
    assert runner.run(VIDEO_INFO, "{}").text == "Error: There is no tool named video_information in this chat."
    assert runner.run(ZOOM_FRAME, "not json").text.startswith("Error:")
