import subprocess
from pathlib import Path

import imageio_ffmpeg

from sceneaccess import transcript
from sceneaccess.session_store import VideoSessionStore
from sceneaccess.transcript import extract_audio_chunks, make_transcript, needs_transcript, transcript_lines


def _video(path: Path, *, sound: bool, seconds: int = 3) -> Path:
    inputs = ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=5"]
    if sound:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", *inputs, "-pix_fmt", "yuv420p",
                    "-shortest", "-y", str(path)], check=True)
    return path


class FakeCatalog:
    def __init__(self, hears=None, audio_models=()):
        self.hears, self.audio_models = hears, list(audio_models)

    def hears_audio(self, model):
        return self.hears

    def audio_video_tool_models(self):
        return self.audio_models


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


def test_only_models_that_are_known_to_hear_get_no_transcript(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    store = VideoSessionStore(tmp_path / "data")
    glm = store.create_session(owner_id="o", source_path=video, title="T", model="z-ai/glm", initial_question="?")
    auto = store.create_session(owner_id="o", source_path=video, title="T", model="openrouter/auto",
                                initial_question="?", audio_only=True)
    assert needs_transcript(glm, FakeCatalog(hears=False)) is True
    assert needs_transcript(glm, FakeCatalog(hears=True)) is False
    # Unknown, e.g. because the model list could not be loaded: a transcript is the safe choice.
    assert needs_transcript(glm, FakeCatalog(hears=None)) is True
    assert needs_transcript(auto, FakeCatalog(audio_models=["google/gemini"])) is False
    assert needs_transcript(auto, FakeCatalog(audio_models=[])) is False
