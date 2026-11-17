import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from sceneaccess import transcript
from sceneaccess.models import ModelCatalog
from sceneaccess.session_store import VideoSessionStore
from sceneaccess.session_store import NO_SOUND_FILE, SOUND_FILE_PREFIX
from sceneaccess.transcript import (extract_audio_chunks, make_transcript, model_hears, sound_parts,
                                    transcript_lines, wants_transcript, with_sound)


def _video(path: Path, *, sound: bool, seconds: int = 3) -> Path:
    inputs = ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=5"]
    if sound:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", *inputs, "-pix_fmt", "yuv420p",
                    "-shortest", "-y", str(path)], check=True)
    return path


class FakeCatalog:
    def __init__(self, hears=None, ids=()):
        self.hears, self.ids, self.asked = hears, list(ids), []

    def hears_audio(self, model):
        self.asked.append(model)
        return self.hears

    def model_ids(self):
        return self.ids


def test_words_are_grouped_into_timed_lines_at_sentence_ends_and_pauses():
    words = [{"word": "All", "start": 1.4, "end": 1.5}, {"word": "right.", "start": 1.6, "end": 1.8},
             {"word": "Here", "start": 2.0, "end": 2.2}, {"word": "we", "start": 2.3, "end": 2.4},
             {"word": "are", "start": 65.0, "end": 65.3}, {"word": " ", "start": 66, "end": 66}]
    assert transcript_lines(words) == ["[00:01] All right.", "[00:02] Here we", "[01:05] are"]


def test_a_video_without_sound_has_no_audio_chunks(tmp_path: Path):
    assert extract_audio_chunks(_video(tmp_path / "silent.mp4", sound=False), tmp_path) == []


def test_long_sound_is_split_and_every_piece_keeps_its_start_time(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(transcript, "CHUNK_SECONDS", 1)
    video = _video(tmp_path / "tone.mp4", sound=True)
    chunks = extract_audio_chunks(video, tmp_path, chunk_seconds=1)
    # MP3 padding can leave a last piece of a few milliseconds.
    assert [offset for offset, _path in chunks][:3] == [0.0, 1.0, 2.0] and len(chunks) <= 4

    class FakeClient:
        def transcribe(self, audio, audio_format):
            assert audio_format == "mp3" and audio
            return {"words": [{"word": "Beep.", "start": 0.5, "end": 0.6}]}

    store = VideoSessionStore(tmp_path / "data")
    session = store.create_session(owner_id="o", source_path=video, title="T", model="m", initial_question="?")
    lines = make_transcript(session, FakeClient()).splitlines()
    # Each piece's word at 0.5 s is shifted by the piece's start.
    assert lines[:3] == ["[00:00] Beep.", "[00:01] Beep.", "[00:02] Beep."]


def test_only_models_that_are_known_to_hear_count_as_hearing(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    store = VideoSessionStore(tmp_path / "data")
    glm = store.create_session(owner_id="o", source_path=video, title="T", model="z-ai/glm", initial_question="?")
    hearing_auto = store.create_session(owner_id="o", source_path=video, title="T", model="openrouter/auto",
                                        initial_question="?", audio_only=True)
    auto = store.create_session(owner_id="o", source_path=video, title="T", model="openrouter/auto",
                                initial_question="?")
    assert model_hears(glm, FakeCatalog(hears=True)) is True
    assert model_hears(glm, FakeCatalog(hears=False)) is False
    # Unknown, e.g. because the model list could not be loaded: it may not hear.
    assert model_hears(glm, FakeCatalog(hears=None)) is False
    # The real catalog decides what openrouter/auto may hear; its model list is not even needed.
    def offline(request, timeout):
        raise OSError("offline")
    catalog = ModelCatalog("https://openrouter.ai/api/v1/chat/completions", opener=offline)
    # Limited to hearing models, openrouter/auto hears; unlimited, it may pick one that only sees the picture.
    assert model_hears(hearing_auto, catalog) is True and model_hears(auto, catalog) is False


def test_a_llama_cpp_server_answers_for_its_one_model_whatever_the_chat_calls_it(tmp_path: Path):
    from dataclasses import replace
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    chat = replace(VideoSessionStore(tmp_path / "data").create_session(
        owner_id="o", source_path=video, title="T", model="", initial_question="?", provider="llama.cpp"),
        model="default")
    single = FakeCatalog(hears=True, ids=[r"D:\models\omni.gguf"])
    assert model_hears(chat, single) is True and single.asked == [r"D:\models\omni.gguf"]
    # With several models, or none because the server is off, only a model it lists by that name counts.
    several = FakeCatalog(hears=None, ids=["a", "b"])
    assert model_hears(chat, several) is False and several.asked == ["default"]
    assert model_hears(chat, FakeCatalog(hears=None)) is False


def test_the_transcript_mode_overrides_whether_the_model_hears(tmp_path: Path):
    from dataclasses import replace
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    glm = VideoSessionStore(tmp_path / "data").create_session(owner_id="o", source_path=video, title="T",
                                                              model="z-ai/glm", initial_question="?")
    for hears in (False, True):
        assert wants_transcript(replace(glm, transcribe="auto"), hears) is not hears
        assert wants_transcript(replace(glm, transcribe="on"), hears) is True
        assert wants_transcript(replace(glm, transcribe="off"), hears) is False


def test_the_sound_goes_right_after_the_video():
    video = {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,AA"}}
    sound = [{"type": "input_audio", "input_audio": {"data": "BB", "format": "mp3"}}]
    messages = [{"role": "system", "content": "Prompt"},
                {"role": "user", "content": [{"type": "text", "text": "?"}, video]},
                {"role": "user", "content": [{"type": "text", "text": "later"}, video]}]
    sent = with_sound(messages, sound)
    assert sent[1]["content"] == [{"type": "text", "text": "?"}, video, *sound]
    # Only the first video gets it, and the stored messages stay as they were.
    assert sent[2] == messages[2] and messages[1]["content"] == [{"type": "text", "text": "?"}, video]
    assert with_sound(messages, []) == messages


def test_the_sound_is_extracted_once_and_goes_with_the_video(tmp_path: Path, monkeypatch):
    store = VideoSessionStore(tmp_path / "data")
    session = store.create_session(owner_id="o", source_path=_video(tmp_path / "tone.mp4", sound=True), title="T",
                                   model="m", initial_question="?")
    parts = sound_parts(session)
    assert parts and parts[0]["type"] == "input_audio" and parts[0]["input_audio"]["format"] == "mp3"
    assert list(session.directory.glob(f"{SOUND_FILE_PREFIX}*.mp3"))
    # Kept beside the video: the next request sends the same without running ffmpeg again.
    monkeypatch.setattr(transcript, "extract_audio_chunks", lambda *a, **kw: pytest.fail("extracted again"))
    assert sound_parts(session) == parts
    # A new stored video brings its own sound.
    store.replace_video(session.id, "o", _video(tmp_path / "other.mp4", sound=True))
    assert not list(session.directory.glob(f"{SOUND_FILE_PREFIX}*"))


def test_a_video_without_sound_is_noted_so_it_is_not_extracted_again(tmp_path: Path, monkeypatch):
    store = VideoSessionStore(tmp_path / "data")
    session = store.create_session(owner_id="o", source_path=_video(tmp_path / "silent.mp4", sound=False),
                                   title="T", model="m", initial_question="?")
    assert sound_parts(session) == [] and (session.directory / NO_SOUND_FILE).exists()
    monkeypatch.setattr(transcript, "extract_audio_chunks", lambda *a, **kw: pytest.fail("extracted again"))
    assert sound_parts(session) == []
    # A chat with its stored sound can still be deleted completely.
    store.delete_session(session.id, "o")
    assert not session.directory.exists()
