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


def test_a_copied_part_heading_is_cut_from_a_sections_description():
    from sceneaccess.sections import without_part_heading
    assert without_part_heading("Part 2 (00:28.1 bis 00:56.1):\n\n00:28 Sie hält ein Tuch.") == "00:28 Sie hält ein Tuch."
    assert without_part_heading("**Teil 3 von 3**\n00:56 Sieb.") == "00:56 Sieb."
    assert without_part_heading("00:00 Part 1 of her day.") == "00:00 Part 1 of her day."


def test_a_whole_video_comes_before_its_text_and_its_sound_after_it(tmp_path: Path):
    from sceneaccess.transcript import with_sound
    store, session = _session(tmp_path, seconds=0, transcribe="on")
    store.update_transcript(session.id, "o", "done", "[00:05] Hello.")
    session = store.get_session(session.id, "o")
    content = with_sound(session.messages, [{"type": "input_audio", "input_audio": {"data": "x", "format": "mp3"}}]
                         )[-1]["content"]
    # As Gemma 4's model card asks: the video, then the question, transcript and title, then the sound.
    assert [part["type"] for part in content] == ["video_url", "text", "text", "text", "input_audio"]
    assert content[1]["text"] == "Describe it." and "[00:05] Hello." in content[2]["text"]
    assert content[3]["text"].endswith("describe nothing from it that cannot be seen: T")
    # The cache breakpoint is on the last part before the sound, so everything up to it is cached together.
    assert [index for index, part in enumerate(content) if "cache_control" in part] == [3]


def test_the_reviewer_on_the_users_own_server_checks_without_thinking(tmp_path: Path):
    store, session = _session(tmp_path, seconds=30, provider="llama.cpp")
    cut = _fixture_sections(session, count=1)
    routings = []

    class Routed(Scripted):
        def run_turn(self, session, messages, run_tool, on_event=None, routing=None, **kwargs):
            routings.append((json.dumps(messages), routing))
            return super().run_turn(session, messages, run_tool, on_event, routing, **kwargs)

    describe_in_sections(Routed(describe=["00:01 A cup."], check=["OK"]), session, "Describe it.", cut, Runner,
                         lambda _progress: None, duration=30.0)
    check = [routing for text, routing in routings if "You are the reviewer" in text]
    describe = [routing for text, routing in routings if "describe part 1 of 1" in text]
    assert check == [{"chat_template_kwargs": {"enable_thinking": False}}]
    # The describer still thinks.
    assert describe and all("chat_template_kwargs" not in (routing or {}) for routing in describe)


def test_the_reviewer_gets_the_title_and_may_report_a_contradiction_to_it():
    from sceneaccess.section_review import REVIEWER_SYSTEM_PROMPT, review_messages
    part = review_messages("00:09 Sie hält die Tube mit beiden Händen.", "", 1, 0, 30, "Beschreibe das Video.",
                           "Mein Einarmiger Alltag")[1]["content"]
    assert "The video's title: Mein Einarmiger Alltag" in part
    assert "e. Does it clearly contradict the title or the transcript" in REVIEWER_SYSTEM_PROMPT
    # And whether a line only says what the blind person would guess from what they hear.
    assert "f. Does it only say what the blind person would guess" in REVIEWER_SYSTEM_PROMPT


def test_a_model_that_hears_is_not_told_it_cannot_hear(tmp_path: Path):
    store, session = _session(tmp_path, seconds=0, transcribe="on")
    store.update_transcript(session.id, "o", "done", "[00:05] Hello.")
    session = store.get_session(session.id, "o")
    assert "You cannot hear" in json.dumps(session.messages)
    heard = json.dumps(replace(session, hears_sound=True).messages)
    assert "You cannot hear" not in heard and "[00:05] Hello." in heard and "the video is what counts" in heard


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
        describe_in_sections(failing, session, "Describe it.", cut, Runner, save, duration=90.0, sound=sound,
                             review=False)
    # The first section was kept as soon as it was described.
    assert [len(progress["sections"]) for progress in saved] == [1]
    first = json.dumps(failing.requests[0][1])
    assert "describe part 1 of 3" in first and "[00:05] Hello." in first and "[00:40]" not in first
    # Whole seconds in the task, so the model does not copy tenths.
    assert "from 00:00 to 00:30" in first and "01:30 long" in first
    assert "input_audio" in first and "video_url" in first

    model = Recorder()
    turn = describe_in_sections(model, store.get_session(session.id, "o"), "Describe it.", cut, Runner, save,
                                duration=90.0, sound=sound, review=False)
    # Continued with parts 2 and 3, each knowing what was said before, then put together without a video.
    texts = [json.dumps(messages) for _session, messages in model.requests]
    assert "describe part 2 of 3" in texts[0] and "Description 1." in texts[0] and "[00:40] Middle." in texts[0]
    assert "describe part 3 of 3" in texts[1]
    assert "give the one answer" in texts[2] and "video_url" not in texts[2] and "input_audio" not in texts[2]
    # The title goes along as a hint, to the sections and to the merge.
    assert "nothing from it that cannot be seen: T\"" in texts[0]
    assert "add nothing from it that they do not say: T" in texts[2]
    # As Gemma 4's model card asks: the video first, then the text with the task last, the sound after it.
    content = model.requests[0][1][-1]["content"]
    assert content[0]["type"] == "video_url" and content[-1]["type"] == "input_audio"
    assert content[-2]["text"].startswith("Your task: describe part 2 of 3")
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


class Scripted:
    """A model that answers each request with the next answer given for its kind: "describe", "check" or "fix";
    raises at a request whose text has fail_on in it."""

    def __init__(self, fail_on=None, **answers):
        self.requests, self.answers, self.fail_on = [], {kind: list(texts) for kind, texts in answers.items()}, fail_on

    def run_turn(self, session, messages, run_tool, on_event=None, routing=None, max_tool_rounds=6, on_delta=None):
        text = json.dumps(messages)
        self.requests.append(messages)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("fixture: server restarted")
        kind = ("merge" if "give the one answer" in text else "check" if "You are the reviewer" in text
                else "fix" if "Your first description" in text else "describe")
        answer = self.answers[kind].pop(0) if kind != "merge" else "Whole."
        # An answer given as CUT is what a server sends when the token or context limit stops it.
        finish = "length" if answer is CUT else "stop"
        answer = "" if answer is CUT else answer
        return Turn(entries=[{"role": "assistant", "content": answer, "thinking": f"{kind} thoughts"}],
                    completion=Completion(answer, "m", 1, None, None, finish_reason=finish))


CUT = object()


def test_a_check_cut_off_never_passes_and_only_the_mechanical_check_counts(tmp_path: Path):
    store, session = _session(tmp_path, seconds=30)
    cut = _fixture_sections(session, count=2)
    model = Scripted(describe=["00:01 A cup.", "00:31 Sie erwähnt den Teller."], check=[CUT, CUT],
                     fix=["00:32 She lifts a plate."])
    describe_in_sections(model, session, "Describe it.", cut, Runner,
                         lambda progress: store.save_sections(session.id, "o", progress), duration=60.0)
    first, second = store.get_session(session.id, "o").sections["sections"]
    # Nothing mechanical in section 1 and its check cut off: it stays, marked as not checked.
    assert first["review"]["failed"] == "cut off at the token limit" and first["review"]["found"] == ""
    # Section 2 is described anew for what the mechanical check found, the cut-off check aside.
    assert "says what is said" in second["review"]["found"] and second["text"] == "00:32 She lifts a plate."
    rows = [row[0] for row in chat_history_rows("Describe it.", [{"role": "assistant", "content": "Whole."}],
                                                 sections=store.get_session(session.id, "o").sections)]
    assert any(row.startswith("Section 1 (00:00 to 00:30, check failed)") for row in rows)
    assert not any(row.startswith("Section 1 (00:00 to 00:30, checked)") for row in rows)


def test_putting_the_sections_together_cut_off_is_no_answer_and_sending_again_only_merges(tmp_path: Path):
    from sceneaccess.openrouter import AnswerCutOff
    store, session = _session(tmp_path, seconds=30)
    cut = _fixture_sections(session, count=2)

    class MergeCut(Scripted):
        def run_turn(self, session, messages, *args, **kwargs):
            if "give the one answer" in json.dumps(messages):
                self.requests.append(messages)
                return Turn(entries=[{"role": "assistant", "content": ""}],
                            completion=Completion("", "m", 1, None, None, finish_reason="length"))
            return super().run_turn(session, messages, *args, **kwargs)

    def save(progress):
        store.save_sections(session.id, "o", progress)

    with pytest.raises(AnswerCutOff, match="Putting the sections together was cut off"):
        describe_in_sections(MergeCut(describe=["00:01 A cup.", "00:31 A plate."], check=["OK", "OK"]), session,
                             "Describe it.", cut, Runner, save, duration=60.0)
    model = Scripted()
    turn = describe_in_sections(model, store.get_session(session.id, "o"), "Describe it.", cut, Runner, save,
                                duration=60.0)
    # Every section and check was kept, so the only request is the merge.
    assert len(model.requests) == 1 and turn.completion.answer == "Whole."


def test_a_description_cut_off_is_not_stored_and_one_described_anew_cut_off_keeps_the_first(tmp_path: Path):
    from sceneaccess.openrouter import AnswerCutOff
    store, session = _session(tmp_path, seconds=30)
    cut = _fixture_sections(session, count=2)

    def save(progress):
        store.save_sections(session.id, "o", progress)

    with pytest.raises(AnswerCutOff, match="section 2 was cut off"):
        describe_in_sections(Scripted(describe=["00:01 A cup.", CUT], check=["OK"]), session, "Describe it.", cut,
                             Runner, save, duration=60.0)
    assert [entry["index"] for entry in store.get_session(session.id, "o").sections["sections"]] == [0]
    describe_in_sections(Scripted(describe=["00:31 Sie erwähnt den Teller."], check=["OK"], fix=[CUT]),
                         store.get_session(session.id, "o"), "Describe it.", cut, Runner, save, duration=60.0)
    second = store.get_session(session.id, "o").sections["sections"][1]
    assert second["text"] == "00:31 Sie erwähnt den Teller." and second["review"]["fix_failed"] is True
    rows = [row[0] for row in chat_history_rows("Describe it.", [{"role": "assistant", "content": "Whole."}],
                                                 sections=store.get_session(session.id, "o").sections)]
    assert any("check found problems, describing it again failed" in row for row in rows)


def test_a_section_that_only_retells_is_checked_and_described_anew_with_its_video(tmp_path: Path):
    store, session = _session(tmp_path, seconds=30, transcribe="on")
    store.update_transcript(session.id, "o", "done", "[00:05] Hello there my dear friends.\n[00:40] Look.")
    session = store.get_session(session.id, "o")
    cut = _fixture_sections(session, count=2)
    model = Scripted(describe=['00:05 "Hello there my dear friends."', "00:30 She holds a sieve.\n00:40 She points."],
                     check=["- 00:05: retells what is said.", "OK"], fix=["00:05 She waves into the camera."])
    turn = describe_in_sections(model, session, "Describe it.", cut, Runner,
                                lambda progress: store.save_sections(session.id, "o", progress), duration=60.0)
    requests = [json.dumps(messages) for messages in model.requests]
    # The check sees neither the video nor the chat: only the description, the transcript and the rules.
    check = model.requests[1]
    # Its own system prompt tells it who it is; the describer's prompt ("Base.") is not sent to it.
    assert [message["role"] for message in check] == ["system", "user"] and "video_url" not in requests[1]
    assert check[0]["content"].startswith("You are the reviewer") and "Base." not in requests[1]
    assert "Hello there my dear friends." in requests[1] and "from 00:00 to 00:30" in requests[1]
    # Both checks found something, so the section went back to the model with its video and what was found.
    fix = requests[2]
    assert "video_url" in fix and "Your first description" in fix and "describe part 1 of 2" in fix
    assert "only repeats what is said" in fix and "retells what is said" in fix
    progress = store.get_session(session.id, "o").sections
    first, second = progress["sections"]
    assert first["text"] == "00:05 She waves into the camera." and first["thinking"] == "fix thoughts"
    assert first["review"]["first_text"] == '00:05 "Hello there my dear friends."'
    assert second["review"]["found"] == "" and second["text"].startswith("00:30 She holds")
    # The next section knows the description anew, not the first one.
    assert "She waves" in requests[3] and "Hello there my dear" not in requests[3].split("video_url")[0]
    assert progress["joined"] == "00:05 She waves into the camera.\n00:30 She holds a sieve.\n00:40 She points."
    # Every request counts: describe, check and fix, describe and check, and the merge.
    assert turn.completion.requests == 6 and len(model.requests) == 6
    rows = chat_history_rows("Describe it.", [{"role": "assistant", "content": "Whole."}], sections=progress)
    shown = [row[0] for row in rows if row[3] == SECTION]
    assert shown[0].startswith("Section 1 (00:00 to 00:30, described again after the check)")
    assert shown[1].startswith("Check of section 1, found, Enter shows it:")
    assert shown[2].startswith("Section 2 (00:30 to 01:00, checked)")
    assert shown[3].startswith("Sections joined without the model")


def test_a_reviewer_model_of_its_own_checks_with_text_only(tmp_path: Path):
    from sceneaccess.sections import Reviewer
    store, session = _session(tmp_path, seconds=30)
    cut = _fixture_sections(session, count=2)
    writer = Scripted(describe=["00:01 A cup.", "00:31 A plate."], fix=["00:02 She lifts a cup."])
    checks = []

    class OtherModel:
        def run_turn(self, session, messages, run_tool, on_event=None, routing=None, max_tool_rounds=6,
                     on_delta=None):
            checks.append((session.model, routing, messages))
            answer = "- 00:01: says nothing about what is seen." if len(checks) == 1 else "OK"
            return Turn(entries=[{"role": "assistant", "content": answer}],
                        completion=Completion(answer, session.model, 1, None, None))

    describe_in_sections(writer, session, "Describe it.", cut, Runner,
                         lambda progress: store.save_sections(session.id, "o", progress), duration=60.0,
                         reviewer=Reviewer(OtherModel(), "vendor/reviewer", {"provider": {"sort": "price"}}))
    # The checks went to the reviewer, with its model and routing and without the video; the writer was asked
    # only to describe, once anew where the reviewer found something.
    assert [(model, routing) for model, routing, _messages in checks] == [("vendor/reviewer", {"provider": {"sort": "price"}})] * 2
    assert all("video_url" not in json.dumps(messages) for _model, _routing, messages in checks)
    assert not any("You are the reviewer" in json.dumps(messages) for messages in writer.requests)
    progress = store.get_session(session.id, "o").sections
    assert progress["sections"][0]["text"] == "00:02 She lifts a cup."
    rows = chat_history_rows("Describe it.", [{"role": "assistant", "content": "Whole."}], sections=progress)
    assert any(row[0].startswith("Check of section 1 by vendor/reviewer, found") for row in rows)


def test_a_description_stopped_before_its_check_is_checked_when_continued(tmp_path: Path):
    store, session = _session(tmp_path, seconds=30)
    cut = _fixture_sections(session, count=2)

    def save(progress):
        store.save_sections(session.id, "o", progress)

    failing = Scripted(fail_on="Part 1 of the video", describe=["00:01 A cup."])
    with pytest.raises(RuntimeError):
        describe_in_sections(failing, session, "Describe it.", cut, Runner, save, duration=60.0)
    model = Scripted(describe=["00:31 A plate."], check=["OK", "OK"])
    describe_in_sections(model, store.get_session(session.id, "o"), "Describe it.", cut, Runner, save, duration=60.0)
    kinds = [json.dumps(messages) for messages in model.requests]
    # Section 1 is not described again, only checked.
    assert "Part 1 of the video" in kinds[0] and "describe part 1 of 2" not in " ".join(kinds)
    assert [entry["review"]["found"] for entry in store.get_session(session.id, "o").sections["sections"]] == ["", ""]


def test_the_mechanical_check_finds_repeated_speech_and_wrong_times():
    from sceneaccess.section_review import mechanical_findings
    heard = "[00:00] So und dann mach ich das drauf.\n[00:26] Ich kann keinen Kabelstaubsauger haben."
    found = mechanical_findings('00:00 "So und dann mach ich das drauf."\n00:09 Sie hält eine Tube hoch.\n'
                                "00:33.0 Ein Staubsauger.\n00:13 Sie zeigt auf den Deckel.", heard, 0, 28.1)
    assert len(found) == 3
    assert "only repeats what is said" in found[0] and "without tenths" in found[1] and "outside the part" in found[2]
    # A time outside the part does not make the next ones out of order; a time before the one before does.
    assert "out of order" in mechanical_findings("00:20 Eins.\n00:10 Zwei.", "", 0, 30)[0]
    assert mechanical_findings("Nur Text ohne Zeit.", "", 0, 30) == [
        "- The description gives no times; start each line with the time as mm:ss."]
    # Saying what is seen is fine.
    assert mechanical_findings("00:26 Sie zeigt auf einen Akku-Staubsauger an der Wand.", heard, 0, 30) == []
    # Saying what someone talks about is not: the blind person hears it.
    for line in ("00:09 Sie erwähnt den Deckel.", "00:19 Sie hält die Tube, während sie über das Drehen spricht.",
                 "00:26 She mentions a corded vacuum cleaner.", "00:23 Sie erklärt, wie sie die Tube hält.",
                 "00:19 Sie hält die Tube, während sie über das „Draufdrehen“ spricht.",
                 "00:23 She says she can manage it.", "00:15 Sie betont, dass der Deckel oft einzeln liegt.",
                 "00:19 Sie beschreibt das Aufdrehen als mühsam."):
        assert "says what is said" in mechanical_findings(line, heard, 0, 30)[0], line
    # Words that also fit what is seen are left to the reviewer, which sees the context.
    for line in ('00:05 Ein Schild an der Tür says "Open".', "00:06 Das Make-up betont ihre Augen.",
                 "00:07 Sie spricht in die Kamera und lächelt.", "00:08 Sie zeigt auf den Deckel.",
                 "00:09 Sie beschreibt mit der Hand einen Kreis.", "00:10 Sie zeigt, dass sie nur einen Arm hat."):
        assert mechanical_findings(line, "", 0, 30) == [], line


def test_the_sections_are_joined_without_what_repeats_where_two_meet():
    from sceneaccess.section_review import joined_without_repeats
    joined = joined_without_repeats([
        {"text": "00:44 Sie hält ein Sieb hoch.\n00:51 Sie deutet auf ihre Prothese an der linken Hand."},
        {"text": "00:56 Sie deutet auf ihre Prothese an der linken Hand.\n01:00 Töpfe.\n"
                 "01:20 Sie hält ein Sieb hoch."}])
    # Lines that differ in one word of a few are different lines.
    assert joined_without_repeats([{"text": "00:10 Part 1."}, {"text": "00:30 Part 2."}]) == "00:10 Part 1.\n00:30 Part 2."
    # Something that happens again later in a section stays.
    assert joined.splitlines() ==["00:44 Sie hält ein Sieb hoch.", "00:51 Sie deutet auf ihre Prothese an der linken "
                                   "Hand.", "01:00 Töpfe.", "01:20 Sie hält ein Sieb hoch."]


def test_a_reviewer_that_finds_nothing_lists_nothing():
    from sceneaccess.section_review import reviewer_findings
    # However a model says it found nothing, in the question's language too, it is no finding.
    for nothing in ("OK", " ok. ", "**OK**", "OK, keine Fehler.", "Keine Verstöße gefunden.", "",
                    "- Keine Verstöße.", "- None.", "* OK", "- No problems."):
        assert reviewer_findings(nothing) == []
    # A finding that starts with such a word still names what is wrong.
    assert reviewer_findings("- Keine Zeitangabe bei 00:13.") == ["- Keine Zeitangabe bei 00:13."]
    # A reviewer going through the lines and its four questions writes a verdict for each; those that are fine
    # are no findings.
    assert reviewer_findings("- 00:13: OK\n* a. Problem: No\n- 00:15: No issues\n- 00:19: spricht über das Drehen") == [
        "- 00:19: spricht über das Drehen"]
    # The questions ask for problems, so yes is a finding and no is not, whatever the question.
    assert reviewer_findings("- 00:19: b. Speech: Yes\n- 00:13: b. Speech: No\n* d. Misspelt: nein") == [
        "- 00:19: b. Speech: Yes"]
    assert reviewer_findings("Problems:\n- 00:13: retells\n* 00:26: quotes") == ["- 00:13: retells", "- 00:26: quotes"]


def test_the_reviewer_is_not_told_nothing_is_said_when_it_gets_no_transcript():
    from sceneaccess.section_review import review_messages

    def review_request(*args):
        return review_messages(*args)[1]["content"]
    assert "nothing is said in this part" in review_request("00:01 A cup.", "", 1, 0, 30, "Describe it.")
    without = review_request("00:01 A cup.", None, 1, 0, 30, "Describe it.")
    assert "no transcript is available" in without and "nothing is said" not in without


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
    assert items[0][0].startswith("Section 1 (00:00 to 00:28, unfinished), Enter shows it: word word")
    assert items[0][0].endswith("…") and items[0][1].endswith("word word")
    answered = chat_history_rows("Describe it.", [{"role": "assistant", "content": "All."}], sections=progress)
    assert "unfinished" not in answered[1][0]
