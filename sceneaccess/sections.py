"""A video too long for the model in one piece, described in sections: each section of at most the chat's
section length gets its own request with its own picture and sound, the matching part of the transcript and
everything said about the sections before it; each description is checked and, if anything was found,
described anew (see section_review.py); a last request puts the descriptions together into the answer, and they
are also kept joined without a model.

Each section's description is stored as soon as it is done, together with what it was made for (question,
model, prompt, section length, transcript mode). A description that stops, because it was cancelled, failed or
the server restarted, continues with the next section; if any of those changed, it starts over."""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .openrouter import AnswerCutOff, DeltaHandler, EventHandler, Turn
from .providers import LLAMA_CPP
from .request_control import RequestControl
from .section_review import (REVIEW_FORMAT, clean_description, fix_request, joined_without_repeats,
                             mechanical_findings, review_messages, reviewer_findings, without_lines_at,
                             without_preamble)
from .session_store import SECTION_FILE_PREFIX, TITLE_HINT, TRANSCRIPT_HELP, VideoSession, joined_sections
from .tools import (HD_FRAME, ZOOM_FRAME, ToolResult, format_time, local_video, run_ffmpeg,
                    video_size_and_duration)
from .transcript import sound_parts

# Sections end a little before the limit, as encoding can make a cut a few hundredths of a second longer.
LIMIT_MARGIN = 0.98
TRANSCRIPT_LINE = re.compile(r"^\[(\d+(?::\d{2}){1,2})\]")


@dataclass(frozen=True)
class Reviewer:
    """Who checks the sections' descriptions, when not the model that wrote them: a client, its model and the
    routing for it, e.g. an OpenRouter model for a chat on the user's own server. It gets only text."""
    client: Any
    model: str
    routing: dict[str, Any] | None = None


@dataclass(frozen=True)
class Section:
    index: int
    start: float
    end: float
    path: Path


def video_duration(session: VideoSession) -> float:
    with local_video(session) as video:
        return video_size_and_duration(video)[1]


def needs_sections(session: VideoSession, duration: float) -> bool:
    """Only a video longer than the chat's section length is described in sections; others go whole."""
    return session.section_seconds > 0 and duration > session.section_seconds


def section_bounds(duration: float, seconds: int) -> list[tuple[float, float]]:
    """Sections of equal length, none longer than seconds. Equal rather than full sections and a short rest: a
    rest of a few seconds would be a part with hardly anything in it, and adding it to the section before would
    make that one longer than the model takes."""
    count = max(1, math.ceil(duration / (seconds * LIMIT_MARGIN)))
    length = duration / count
    return [(index * length, duration if index == count - 1 else (index + 1) * length) for index in range(count)]


def cut_sections(session: VideoSession, duration: float, control: RequestControl | None = None) -> list[Section]:
    """The chat's sections as files beside its video, cut once and then reused. Encoded anew rather than copied:
    copying can only cut at key frames, which would move each section by seconds."""
    sections = []
    for index, (start, end) in enumerate(section_bounds(duration, session.section_seconds)):
        path = session.directory / f"{SECTION_FILE_PREFIX}{session.section_seconds}s-{index:03d}.mp4"
        if not path.exists():
            # Written under another name first, so a failed cut never looks like a finished section.
            partial = path.with_name(path.stem + ".tmp.mp4")
            with local_video(session) as video:
                done = run_ffmpeg(["-ss", f"{start:.3f}", "-i", str(video), "-t", f"{end - start:.3f}",
                                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                                   "-c:a", "aac", "-b:a", "96k", "-y", str(partial)], timeout=900,
                                  **({"control": control} if control else {}))
            if done.returncode != 0 or not partial.exists():
                partial.unlink(missing_ok=True)
                raise RuntimeError(done.stderr.strip() or f"ffmpeg could not cut section {index + 1}.")
            partial.replace(path)
        sections.append(Section(index, start, end, path))
    return sections


def section_sound(session: VideoSession, section: Section, control: RequestControl | None = None) -> list[dict[str, Any]]:
    """A section's own sound as input_audio parts, for a model on a llama.cpp server that hears."""
    return sound_parts(session, control, source=section.path, prefix=f"{section.path.stem}-sound-")


def transcript_between(transcript: str, start: float, end: float) -> str:
    """The lines of a transcript like "[01:23] What was said" that start within [start, end)."""
    lines = []
    for line in transcript.splitlines():
        found = TRANSCRIPT_LINE.match(line)
        if found:
            parts = [int(part) for part in found.group(1).split(":")]
            seconds = sum(part * 60 ** power for power, part in enumerate(reversed(parts)))
            if start <= seconds < end:
                lines.append(line)
    return "\n".join(lines)


def progress_key(session: VideoSession, question: str, model_name: str = "") -> dict[str, Any]:
    """What a section's description depends on; stored progress for anything else is not continued. model_name
    is the model the server says it runs, which for a llama.cpp chat on "default" tells two models apart."""
    return {"question": question, "seconds": session.section_seconds, "model": model_name or session.model,
            "system_prompt": session.system_prompt, "transcribe": session.transcribe,
            "hears": session.hears_sound}


# The task comes last, after everything it refers to: small models weigh what they read last most.
SECTION_TASK = ("\n\nYour task: describe part {number} of {count}, from {span}, of the video, which is {total} long "
                "and comes in {count} parts, each with its own picture and sound. The video is what counts: describe "
                "what can be seen in it, the actions, aids, objects and details that matter. What is said, and the "
                "transcript, only help to understand it; do not retell them. The blind person hears the speech, so "
                "no line says what she or he says, mentions, explains or talks about, not even as \"while she talks "
                "about …\"; if nothing new can be seen at a moment, leave that moment out. Leave out, too, what the "
                "blind person would guess from what they hear, such as someone looking at, pointing to or holding "
                "what they are talking about; mention a moment only if it tells them something of value they could "
                "not know otherwise. "
                "Give every time as mm:ss in the whole video, between {start} and {end}, at the moments when "
                "something changes in the picture, not at the times of the transcript. Mention what goes on from "
                "the part before only if something new happens.\n\n"
                "How to work: look at the part, then write the description once, from its start to its end. A "
                "moment you have described is done: never think about it again, and when you reach the end of the "
                "part, the description is finished. Do not draft the list several times, do not check each line "
                "against the transcript, and do not go back over what you wrote; over-analysing is the mistake to "
                "avoid, and thinking longer does not make the description better. "
                "Start right away with the first time, with nothing before it; do not repeat this task, the "
                "transcript or what you wrote about earlier parts. Answer in the language of the question: "
                "{question}")
# The merge has no video, so the title must not add anything the sections do not say.
MERGE_TITLE_HINT = ("The video's title, only to help understand the descriptions; add nothing from it that they do "
                    "not say: {title}")
MERGE_TASK = ("\n\nYour task: give the one answer for the whole video, which is {total} long, as if you had seen it "
              "at once: put your descriptions of the parts above together in order, keep every detail and every "
              "time, and leave out only what repeats where two parts meet. Do not mention the parts. Answer in the "
              "language of the question: {question}")
# The merge is the last check (user's wish, 19.11.2026): it alone sees all sections and the whole transcript, so
# it can see what a section got wrong, look at a moment again and have a section described anew.
MERGE_CHECK = ("\n\nBefore you answer, you are the last check. If a line looks wrong, because it says the opposite "
               "of the transcript or the title, or seems made up, look at that moment with zoom_frame, or call "
               "describe_section_again with the part's number and what was wrong; each part at most once, and only "
               "for a real mistake. Then answer with the parts as they are now.")
MERGE_TRANSCRIPT = "An automatic transcript of the whole video; speech recognition may contain errors:\n\n{transcript}"
MERGE_FIX = ("\n\nYour first description of this part:\n\n{text}\n\nThe last check, which saw all parts and the whole "
             "transcript, found this wrong with it:\n\n{found}\n\nLook at the part again and write its description "
             "anew. Correct what was found from what you see; where you cannot see what is there, leave the line "
             "out. Add nothing that is not in the picture, and write the description once, without drafts.")
REDO_SECTION = "describe_section_again"
REDO_TOOL = {"type": "function", "function": {
    "name": REDO_SECTION,
    "description": "Has one part of the video described anew by the model that sees it, with its video, told what was "
                   "wrong with its description; returns the new description. Each part at most once.",
    "parameters": {"type": "object", "properties": {
        "part": {"type": "integer", "description": "The part's number, from 1."},
        "problems": {"type": "string", "description": "What is wrong with the part's description, and at which times."}},
        "required": ["part", "problems"]}}}


PART_HEADING = re.compile(r"\A\s*(?:\*\*)?(?:Part|Teil|Abschnitt)\s+\d+\b[^\n]*\n+", re.IGNORECASE)


def without_part_heading(text: str) -> str:
    """A section's description without a heading such as "Part 2 (00:28 to 00:56):", which models copy from the
    descriptions of the parts before; the Chat history and the merged answer have headings of their own."""
    return PART_HEADING.sub("", text, count=1)


def describe_in_sections(client: Any, session: VideoSession, question: str, sections: list[Section],
                         new_runner: Callable[[], Any], save: Callable[[dict[str, Any]], None], *,
                         duration: float, sound: Callable[[Section], list[dict[str, Any]]] | None = None,
                         on_event: EventHandler | None = None, on_delta: DeltaHandler | None = None,
                         routing: dict[str, Any] | None = None, max_tool_rounds: int = 6,
                         model_name: str = "", review: bool = True, reviewer: Reviewer | None = None) -> Turn:
    """Describes each section not yet described, saving the progress after each one, then asks for the answer
    to question for the whole video. new_runner gives a tools.ToolRunner per section; its images are not kept,
    as only the descriptions are stored. sound gives a section's sound for a model that gets it apart from the
    video; model_name see progress_key. With review, each description is cleaned mechanically and checked (see
    section_review.py), by reviewer if given, else by the same model; lines that add nothing are removed, and
    only a contradiction has the section described anew, once. Before the last request the sections are also
    stored joined without a model, as "joined". Returns the turn of the last request, whose answer answers question and whose
    usage counts all requests."""
    notify = on_event or (lambda _kind, _text: None)
    key = progress_key(session, question, model_name)
    stored = session.sections if session.sections and session.sections.get("key") == key else None
    progress = stored or {"key": key, "sections": []}
    described = progress["sections"]
    # Whole seconds only: the model copies the format, and tenths are noise on a braille display.
    total = format_time(round(duration))

    def ask(section: Section, before: list[dict[str, Any]], heard: str, fix: str = "") -> Turn:
        runner = new_runner()
        try:
            parts = _section_parts(section, len(sections), total, question, before, heard, sound, fix,
                                   session.title)
            return client.run_turn(session, [*session.system_messages, {"role": "user", "content": parts}],
                                   runner.run, on_event, routing, max_tool_rounds=max_tool_rounds, on_delta=on_delta)
        finally:
            runner.cleanup_files()

    def anew(position: int, fix: str) -> tuple[str, str]:
        """Has the section at position described anew with its video, told what was wrong, and cleans the result.
        Returns the outcome, "rewritten", "fix_failed", "fix_worse" or "fix_unchanged", and the description to
        keep."""
        section, entry = sections[position], described[position]
        heard = transcript_between(session.transcript, section.start, section.end) if session.sends_transcript else ""
        turn = ask(section, described[:position], heard, fix)
        entry["usage"] = _added(entry.get("usage"), turn.completion.usage_record())
        written = without_part_heading(turn.completion.answer)
        if turn.completion.cut_off or not written.strip():
            return "fix_failed", entry["text"]
        if len(mechanical_findings(written, heard, section.start, section.end)) > len(
                mechanical_findings(entry["text"], heard, section.start, section.end)):
            # Worse than what it had by what can be counted: that stays.
            return "fix_worse", entry["text"]
        cleaned = clean_description(written, heard)[0]
        if cleaned == clean_description(entry["text"], heard)[0]:
            # Gemma 4 12B once sent the same description back, ignoring what was found.
            return "fix_unchanged", entry["text"]
        entry["thinking"] = turn.entries[-1].get("thinking", "")
        return "rewritten", cleaned

    for position, section in enumerate(sections):
        number = section.index + 1
        span = f"{format_time(round(section.start))} to {format_time(round(section.end))}"
        heard = transcript_between(session.transcript, section.start, section.end) if session.sends_transcript else ""
        if position >= len(described):
            notify("section", f"Section {number} of {len(sections)}, {span}")
            turn = ask(section, described[:position], heard)
            if turn.completion.cut_off or not turn.completion.answer.strip():
                # Not stored: a description cut off at the token limit is no description, and sending the first
                # question again continues with this section.
                notify("section", f"Section {number} was {'cut off' if turn.completion.cut_off else 'empty'}")
                raise AnswerCutOff(f"The description of section {number} was "
                                    f"{'cut off at the token limit' if turn.completion.cut_off else 'empty'}; "
                                    "the sections before it are kept. Send the first question again to continue.")
            described.append({"index": section.index, "start": section.start, "end": section.end,
                              "text": without_part_heading(turn.completion.answer),
                              "thinking": turn.entries[-1].get("thinking", ""),
                              "usage": turn.completion.usage_record()})
            save(progress)
        entry = described[position]
        if not review or "review" in entry:
            continue
        first, first_thinking = entry["text"], entry.get("thinking", "")
        # What needs no model is removed first, mechanically: nothing is replaced, so nothing can be made up.
        cleaned, notes = clean_description(first, heard)
        # The check has no video and no chat, so it judges only the text against the title and what is said.
        notify("section", f"Checking section {number}")
        # The same model on the user's own server checks without thinking: with it, Gemma 4 12B went over the
        # same lines again and again, however the prompt asked it to stop (user's wish, 18.11.2026). Its answer
        # is forced into the JSON form, so its reasoning never reads as findings.
        check_routing = {**((reviewer.routing if reviewer else routing) or {}), **REVIEW_FORMAT}
        if not reviewer and session.provider == LLAMA_CPP:
            check_routing["chat_template_kwargs"] = {"enable_thinking": False}
        check = (reviewer.client if reviewer else client).run_turn(
            replace(session, tools=[], **({"model": reviewer.model} if reviewer else {})),
            review_messages(cleaned, heard if session.sends_transcript else None, number, section.start,
                            section.end, session.title),
            lambda *_: None, on_event, check_routing, max_tool_rounds=0, on_delta=on_delta)
        entry["usage"] = _added(entry.get("usage"), check.completion.usage_record())
        # A check cut off, empty or not in the JSON form never passes: only the mechanical cleaning counts then,
        # and the Chat history says the check failed (Gemma 4 12B once thought until the context was full).
        failed, problems = ("cut off at the token limit" if check.completion.cut_off
                            else "empty" if not check.completion.answer.strip() else ""), []
        if not failed:
            try:
                problems = reviewer_findings(check.completion.answer)
            except ValueError:
                failed = "not in the form asked for"
        if failed:
            notify("section", f"The check of section {number} was {failed}; only the mechanical cleaning counts")
        # A line that only says what the blind person would guess anyway is removed, not rewritten.
        obvious = [problem for problem in problems if problem["question"] == "f"]
        cleaned, removed = without_lines_at(cleaned, [problem["time"] for problem in obvious])
        notes += [f"- Removed, it adds nothing for a blind listener: {line.strip()}" for line in removed]
        contradictions = [problem for problem in problems if problem["question"] == "e"]
        review_record = {"found": "\n".join(notes + [f"- {problem['time']} contradicts the title or the transcript: "
                                                     f"{problem['why']}" for problem in contradictions]),
                         "reviewer": check.completion.answer.strip(), "failed": failed,
                         # Named only when another model checked, so the Chat history says who.
                         "reviewer_model": check.completion.model if reviewer else ""}
        if contradictions:
            # Only a contradiction needs the video again: the model must look, not fill a gap.
            notify("section", f"Describing section {number} again: {len(contradictions)} lines contradict the "
                              "title or the transcript")
            entry["text"] = first
            outcome, written = anew(position, fix_request(cleaned, contradictions))
            review_record[outcome] = True
            if outcome == "rewritten":
                cleaned = written
        if cleaned != first:
            review_record.update(first_text=first, first_thinking=first_thinking)
        entry["text"] = cleaned
        entry["review"] = review_record
        save(progress)
    progress["joined"] = joined_without_repeats(described)
    save(progress)
    notify("section", f"Putting the {len(sections)} sections together")
    # The merge may look at a moment again and have a part described anew: it is the last check.
    merge_tools = [tool for tool in session.tools if tool.get("type") == "function"
                   and tool["function"]["name"] in (ZOOM_FRAME, HD_FRAME)] + ([REDO_TOOL] if review else [])
    redone: set[int] = set()
    runner = new_runner()

    def run_merge_tool(name: str, arguments: str) -> ToolResult:
        if name != REDO_SECTION:
            return runner.run(name, arguments)
        try:
            asked = json.loads(arguments or "{}")
            number = int(asked["part"])
        except (ValueError, KeyError, TypeError):
            return ToolResult("Give the part's number and what is wrong with its description.")
        if not 1 <= number <= len(sections):
            return ToolResult(f"There are parts 1 to {len(sections)}.")
        if number in redone:
            return ToolResult(f"Part {number} was described anew already; use what it says now.")
        redone.add(number)
        position, problems = number - 1, str(asked.get("problems", "")).strip()
        notify("section", f"Describing section {number} again, as the merge asked: {problems}")
        entry = described[position]
        outcome, written = anew(position, MERGE_FIX.format(text=entry["text"], found=problems))
        record = entry.setdefault("review", {})
        record["merge"] = {"problems": problems, "outcome": outcome}
        if outcome == "rewritten":
            record.setdefault("first_text", entry["text"])
            entry["text"] = written
        progress["joined"] = joined_without_repeats(described)
        save(progress)
        if outcome != "rewritten":
            return ToolResult(f"Part {number} could not be described better; keep its description as it is.")
        return ToolResult(f"Part {number} ({format_time(round(entry['start']))} to {format_time(round(entry['end']))}),"
                          f" described anew:\n\n{written}")

    text = (joined_sections(described)
            + ("\n\n" + MERGE_TRANSCRIPT.format(transcript=session.transcript) if session.sends_transcript else "")
            + ("\n\n" + MERGE_TITLE_HINT.format(title=session.title) if session.title else "")
            + MERGE_TASK.format(total=total, question=question) + (MERGE_CHECK if merge_tools else ""))
    try:
        turn = client.run_turn(replace(session, tools=merge_tools),
                               [*session.system_messages, {"role": "user", "content": text}], run_merge_tool,
                               on_event, routing, max_tool_rounds=max_tool_rounds if merge_tools else 0,
                               on_delta=on_delta)
    except BaseException:
        runner.cleanup_files()
        raise
    if turn.completion.cut_off or not turn.completion.answer.strip():
        runner.cleanup_files()
        # Every section and check is stored, so sending the first question again only puts them together anew.
        notify("section", "Putting the sections together was cut off" if turn.completion.cut_off
               else "Putting the sections together gave no answer")
        raise AnswerCutOff("Putting the sections together was "
                            f"{'cut off at the token limit' if turn.completion.cut_off else 'empty'}; every section "
                            "is kept. Send the first question again to put them together anew.")
    # The images the merge looked at belong to the answer now, which the chat stores with them.
    runner.commit_files()
    # Without "Here is the description of the video:", which the merge wrote before the first time each run.
    answer = without_preamble(turn.completion.answer)
    turn.entries[-1]["content"] = answer
    # The usage line under the answer counts every request it took, the sections' included.
    usages = [entry.get("usage") or {} for entry in described]
    completion = replace(turn.completion, answer=answer,
                         requests=turn.completion.requests + sum(int(u.get("requests") or 1) for u in usages),
                         prompt_tokens=turn.completion.prompt_tokens + sum(int(u.get("prompt_tokens") or 0) for u in usages))
    turn.entries[-1]["usage"] = completion.usage_record()
    return Turn(entries=turn.entries, completion=completion)


def _section_parts(section: Section, count: int, total: str, question: str, before: list[dict[str, Any]],
                   heard: str, sound: Callable[[Section], list[dict[str, Any]]] | None,
                   fix: str = "", title: str = "") -> list[dict[str, Any]]:
    """What a section's request holds, in the order Gemma 4's model card asks for, the picture before the text and
    the sound after it: the section's video; the descriptions of the parts before, its transcript, for a
    description anew the first one and what the check found, the title and the task; last its sound."""
    start, end = format_time(round(section.start)), format_time(round(section.end))
    data = base64.b64encode(section.path.read_bytes()).decode("ascii")
    parts: list[dict[str, Any]] = [{"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{data}"}}]
    if before:
        parts.append({"type": "text", "text": "What you wrote about the parts before:\n\n" + joined_sections(before)})
    if heard:
        parts.append({"type": "text", "text": "An automatic transcript of what is said in this part; speech "
                                              "recognition may contain errors, times are those of the whole "
                                              f"video. {TRANSCRIPT_HELP}\n\n" + heard})
    if fix:
        parts.append({"type": "text", "text": fix.strip()})
    if title:
        parts.append({"type": "text", "text": TITLE_HINT.format(title=title)})
    parts.append({"type": "text", "text": SECTION_TASK.format(
        number=section.index + 1, count=count, span=f"{start} to {end}", total=total, start=start, end=end,
        question=question).strip()})
    return parts + (sound(section) if sound else [])


def _added(usage: dict[str, Any] | None, more: dict[str, Any]) -> dict[str, Any]:
    """Two requests' usage as one, for a section that was checked or described anew."""
    usage = usage or {}
    return {**usage, "requests": int(usage.get("requests") or 1) + int(more.get("requests") or 1),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0) + int(more.get("prompt_tokens") or 0)}
