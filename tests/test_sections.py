import json
import subprocess
from dataclasses import replace
from pathlib import Path

import imageio_ffmpeg
import pytest

from sceneaccess import sections
from sceneaccess.chat_history import SECTION, chat_history_rows
from sceneaccess.openrouter import Completion, Turn
from sceneaccess.sections import (Section, cut_sections, describe_in_sections, needs_sections, section_bounds,
                                  transcript_between, video_duration)
from sceneaccess.session_store import SECTION_FILE_PREFIX, VideoSessionStore
from sceneaccess.tools import video_size_and_duration


def _video(path: Path, seconds: int = 7) -> Path:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=duration={seconds}:size=160x120:rate=10", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-pix_fmt", "yuv420p", "-shortest", "-y", str(path)],
                   check=True)
    return path


def _session(tmp_path: Path, seconds: int = 3, video: Path | None = None, **fields):
    store = VideoSessionStore(tmp_path / "data")
    if video is None:
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fixture, no media decoding")
    session = store.create_session(owner_id="o", source_path=video, title="T", model="m", initial_question="Describe it.",
                                   system_prompt="Base.", section_seconds=seconds, **fields)
    return store, session


def test_sections_are_equal_and_never_longer_than_the_model_takes():
    bounds = section_bounds(85.0, 30)
    assert len(bounds) == 3 and bounds[0][0] == 0 and bounds[-1][1] == 85.0
    lengths = [end - start for start, end in bounds]
    assert max(lengths) - min(lengths) < 0.01 and max(lengths) < 30
    # Exactly the limit would come out a little longer once encoded, so it is split too.
    assert all(end - start < 30 for start, end in section_bounds(60.0, 30))
    assert section_bounds(20.0, 30) == [(0.0, 20.0)]


def test_only_a_video_longer_than_a_section_is_described_in_sections(tmp_path: Path):
    _store, session = _session(tmp_path, seconds=30)
    assert needs_sections(session, 85.0) and not needs_sections(session, 30.0)
    assert not needs_sections(replace(session, section_seconds=0), 85.0)


def test_the_video_is_cut_precisely_once_and_the_sections_are_reused(tmp_path: Path, monkeypatch):
    _store, session = _session(tmp_path, seconds=3, video=_video(tmp_path / "seven.mp4"))
    duration = video_duration(session)
    cut = cut_sections(session, duration)
    assert [section.index for section in cut] == [0, 1, 2]
    for section in cut:
        # Encoded anew, so each section is as long as planned, not cut at the nearest key frame.
        assert section.path.name.startswith(f"{SECTION_FILE_PREFIX}3s-") and section.path.parent == session.directory
        assert abs(video_size_and_duration(section.path)[1] - (section.end - section.start)) < 0.3
    monkeypatch.setattr(sections, "run_ffmpeg", lambda *a, **kw: pytest.fail("cut again"))
    assert [section.path for section in cut_sections(session, duration)] == [section.path for section in cut]


def test_each_section_gets_the_transcript_lines_of_its_own_time():
    transcript = "[00:01] One.\n[00:29] Two.\n[00:31] Three.\n[01:05] Four."
    assert transcript_between(transcript, 0, 30) == "[00:01] One.\n[00:29] Two."
    assert transcript_between(transcript, 30, 60) == "[00:31] Three."
    assert transcript_between(transcript, 60, 90) == "[01:05] Four."


class Recorder:
    """A model that describes each part it gets, and fails at the part given in fail_at."""

    def __init__(self, fail_at=None):
        self.requests, self.fail_at = [], fail_at

    def run_turn(self, session, messages, run_tool, on_event=None, routing=None, max_tool_rounds=6, on_delta=None):
        self.requests.append((session, messages))
        text = json.dumps(messages)
        if self.fail_at is not None and f"describe part {self.fail_at} of" in text:
            raise RuntimeError("fixture: server restarted")
        answer = "The whole video." if "give the one answer" in text else f"Description {len(self.requests)}."
        return Turn(entries=[{"role": "assistant", "content": answer, "thinking": "Hm."}],
                    completion=Completion(answer, "m", 1, None, None))


class Runner:
    cleaned = 0

    def run(self, *args):
        raise AssertionError("no tools in this fixture")

    def cleanup_files(self):
        Runner.cleaned += 1


def _fixture_sections(session, count=3, length=30.0):
    found = []
    for index in range(count):
        path = session.directory / f"{SECTION_FILE_PREFIX}30s-{index:03d}.mp4"
        path.write_bytes(b"section %d" % index)
        found.append(Section(index, index * length, (index + 1) * length, path))
    return found


def test_a_description_that_stops_continues_with_the_next_section(tmp_path: Path):
    store, session = _session(tmp_path, seconds=30, transcribe="on")
    store.update_transcript(session.id, "o", "done", "[00:05] Hello.\n[00:40] Middle.\n[01:10] End.")
    session = store.get_session(session.id, "o")
    cut = _fixture_sections(session)
    saved, sounds = [], []

    def save(progress):
        saved.append(json.loads(json.dumps(progress)))
        store.save_sections(session.id, "o", progress)

    def sound(section):
        sounds.append(section.index)
        return [{"type": "input_audio", "input_audio": {"data": "x", "format": "mp3"}}]

    failing = Recorder(fail_at=2)
    with pytest.raises(RuntimeError, match="server restarted"):
        describe_in_sections(failing, session, "Describe it.", cut, Runner, save, duration=90.0, sound=sound)
    # The first section was kept as soon as it was described.
    assert [len(progress["sections"]) for progress in saved] == [1]
    first = json.dumps(failing.requests[0][1])
    assert "describe part 1 of 3" in first and "[00:05] Hello." in first and "[00:40]" not in first
    assert "input_audio" in first and "video_url" in first

    model = Recorder()
    turn = describe_in_sections(model, store.get_session(session.id, "o"), "Describe it.", cut, Runner, save,
                                duration=90.0, sound=sound)
    # Continued with parts 2 and 3, each knowing what was said before, then put together without a video.
    texts = [json.dumps(messages) for _session, messages in model.requests]
    assert "describe part 2 of 3" in texts[0] and "Description 1." in texts[0] and "[00:40] Middle." in texts[0]
    assert "describe part 3 of 3" in texts[1]
    assert "give the one answer" in texts[2] and "video_url" not in texts[2] and "input_audio" not in texts[2]
    # The task comes last, after the video and the sound it refers to.
    assert model.requests[0][1][-1]["content"][-1]["text"].startswith("Your task: describe part 2 of 3")
    assert turn.completion.answer == "The whole video."
    # The usage counts the sections' requests too: three here, the first one's request that failed not.
    assert turn.completion.requests == 4
    stored = store.get_session(session.id, "o").sections["sections"]
    assert [entry["index"] for entry in stored] == [0, 1, 2] and stored[0]["thinking"] == "Hm."
    # Section 2's sound was fetched for the request that failed and again when continuing; in the app it is
    # then read from the file kept beside the video, not extracted again.
    assert sounds == [0, 1, 1, 2]


def test_progress_for_another_question_or_model_is_not_continued(tmp_path: Path):
    store, session = _session(tmp_path, seconds=30)
    cut = _fixture_sections(session, count=2)
    describe_in_sections(Recorder(), session, "Describe it.", cut, Runner,
                         lambda progress: store.save_sections(session.id, "o", progress), duration=60.0)
    for changed, question in ((store.get_session(session.id, "o"), "What brand?"),
                              (replace(store.get_session(session.id, "o"), model="other"), "Describe it.")):
        model = Recorder()
        describe_in_sections(model, changed, question, cut, Runner, lambda _progress: None, duration=60.0)
        assert "describe part 1 of 2" in json.dumps(model.requests[0][1])
    # The model the server says it runs counts, whatever the chat calls it.
    model = Recorder()
    describe_in_sections(model, store.get_session(session.id, "o"), "Describe it.", cut, Runner, lambda _progress: None,
                         duration=60.0, model_name="another.gguf")
    assert "describe part 1 of 2" in json.dumps(model.requests[0][1])


def test_a_chat_described_in_sections_sends_no_video_with_later_questions(tmp_path: Path):
    store, session = _session(tmp_path, seconds=30)
    assert "video_url" in json.dumps(session.messages)
    store.save_sections(session.id, "o", {"key": {}, "sections": [{"index": 0, "start": 0, "end": 30, "text": "A."}]})
    store.record_turn(session.id, "o", [{"role": "assistant", "content": "The whole video."}])
    later = json.dumps(store.build_follow_up_messages(session.id, "o", "And then?"))
    assert "video_url" not in later and "The whole video." in later and "And then?" in later


def test_sections_go_with_the_video_and_with_the_first_answer(tmp_path: Path):
    store, session = _session(tmp_path, seconds=30, video=_video(tmp_path / "seven.mp4"))
    progress = {"key": {}, "sections": [{"index": 0, "start": 0, "end": 30, "text": "A."}]}
    (session.directory / f"{SECTION_FILE_PREFIX}30s-000.mp4").write_bytes(b"x")
    store.save_sections(session.id, "o", progress)
    store.replace_video(session.id, "o", _video(tmp_path / "other.mp4"))
    assert store.get_session(session.id, "o").sections is None
    assert not list(session.directory.glob(f"{SECTION_FILE_PREFIX}*"))
    # Deleting the answer to the first question forgets its sections; so does a new setup.
    store.save_sections(session.id, "o", progress)
    store.record_turn(session.id, "o", [{"role": "assistant", "content": "Answer."}])
    store.delete_rounds(session.id, "o", [0])
    assert store.get_session(session.id, "o").sections is None
    store.save_sections(session.id, "o", progress)
    store.update_setup(session.id, "o", "New prompt.", [])
    assert store.get_session(session.id, "o").sections is None
    # A chat with its section files can still be deleted completely.
    (session.directory / f"{SECTION_FILE_PREFIX}30s-000.mp4").write_bytes(b"x")
    store.delete_session(session.id, "o")
    assert not session.directory.exists()


def test_the_chat_history_shows_the_sections_folded():
    progress = {"key": {}, "sections": [{"index": 0, "start": 0, "end": 28.3, "text": " ".join(["word"] * 40)},
                                        {"index": 1, "start": 28.3, "end": 56.7, "text": "Short."}]}
    rows = chat_history_rows("Describe it.", [], sections=progress)
    items = [(shown, full) for shown, full, _index, kind in rows if kind == SECTION]
    assert items[0][0].startswith("Section 1 (00:00 to 00:28.3, unfinished), Enter shows it: word word")
    assert items[0][0].endswith("…") and items[0][1].endswith("word word")
    answered = chat_history_rows("Describe it.", [{"role": "assistant", "content": "All."}], sections=progress)
    assert "unfinished" not in answered[1][0]
