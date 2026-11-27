"""A video too long for the model in one piece, described in sections: each section of at most the chat's
section length gets its own request with its own picture and sound, the matching part of the transcript and
everything said about the sections before it; each description is cleaned (cleaning.py), checked
(section_review.py) and, if a contradiction was found, described anew; a last request puts the descriptions
together into the answer, and they are also kept joined without a model. Cutting the sections is section_cuts.py.

Each section's description is stored as soon as it is done, together with what it was made for (question,
model, prompt, section length, transcript mode). A description that stops, because it was cancelled, failed or
the server restarted, continues with the next section; if any of those changed, it starts over."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ..llm.client import AnswerCutOff, DeltaHandler, EventHandler, Turn, added_usage
from ..llm.tool_calls import HD_FRAME, ZOOM_FRAME, ToolResult
from ..prompts import (
    MERGE_CHECK,
    MERGE_FIX,
    MERGE_TASK,
    MERGE_TITLE_HINT,
    MERGE_TRANSCRIPT,
    SECTION_TASK,
    SECTION_TRANSCRIPT_INTRO,
    SECTIONS_BEFORE,
    TITLE_HINT,
)
from ..providers import LLAMA_CPP
from ..storage.session_store import VideoSession, joined_sections
from ..timecodes import format_time
from .cleaning import clean_description, joined_without_repeats, mechanical_findings, without_lines_at
from .section_cuts import Section, transcript_between
from .section_review import REVIEW_FORMAT, Reviewer, fix_request, review_messages, reviewer_findings


def progress_key(session: VideoSession, question: str, model_name: str = "") -> dict[str, Any]:
    """What a section's description depends on; stored progress for anything else is not continued. model_name
    is the model the server says it runs, which for a llama.cpp chat on "default" tells two models apart."""
    return {"question": question, "seconds": session.section_seconds, "model": model_name or session.model,
            "system_prompt": session.system_prompt, "transcribe": session.transcribe,
            "hears": session.hears_sound}


def _merge_check(tools: list[dict[str, Any]]) -> str:
    """The merge's task as the last check, naming only the tools it has."""
    names = [tool["function"]["name"] for tool in tools]
    ways = ([f"look at that moment with {' or '.join(name for name in names if name in (ZOOM_FRAME, HD_FRAME))}"]
            if ZOOM_FRAME in names or HD_FRAME in names else [])
    ways += ["call describe_section_again with the part's number and what was wrong"] if REDO_SECTION in names else []
    return MERGE_CHECK.format(how=", or ".join(ways)) if ways else ""


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
    video; model_name see progress_key. With review, each description is cleaned mechanically (cleaning.py) and
    checked (section_review.py), by reviewer if given, else by the same model; lines that add nothing are removed,
    and only a contradiction has the section described anew, once. Before the last request the sections are also
    stored joined without a model, as "joined". Returns the turn of the last request, whose answer answers
    question and whose usage counts all requests."""
    run = _SectionRun(client, session, question, sections, new_runner, save, duration=duration, sound=sound,
                      on_event=on_event, on_delta=on_delta, routing=routing, max_tool_rounds=max_tool_rounds,
                      model_name=model_name, reviewer=reviewer)
    for position in range(len(sections)):
        if position >= len(run.described):
            run.describe(position)
        if review and "review" not in run.described[position]:
            run.check(position)
    return run.merge(review)


class _SectionRun:
    """One description of a video in sections: the sections, their stored progress, and what every request about
    them needs. See describe_in_sections."""

    def __init__(self, client: Any, session: VideoSession, question: str, sections: list[Section],
                 new_runner: Callable[[], Any], save: Callable[[dict[str, Any]], None], *, duration: float,
                 sound: Callable[[Section], list[dict[str, Any]]] | None, on_event: EventHandler | None,
                 on_delta: DeltaHandler | None, routing: dict[str, Any] | None, max_tool_rounds: int,
                 model_name: str, reviewer: Reviewer | None):
        self.client, self.session, self.question, self.sections = client, session, question, sections
        self.new_runner, self.sound, self.reviewer = new_runner, sound, reviewer
        self.on_event, self.on_delta, self.routing, self.max_tool_rounds = on_event, on_delta, routing, max_tool_rounds
        self.notify = on_event or (lambda _kind, _text: None)
        self._save = save
        key = progress_key(session, question, model_name)
        stored = session.sections if session.sections and session.sections.get("key") == key else None
        self.progress = stored or {"key": key, "sections": []}
        self.described = self.progress["sections"]
        # Whole seconds only: the model copies the format, and tenths are noise on a braille display.
        self.total = format_time(round(duration))

    def save(self) -> None:
        self._save(self.progress)

    def heard(self, section: Section) -> str:
        """The transcript of the section, if the chat sends one."""
        return (transcript_between(self.session.transcript, section.start, section.end)
                if self.session.sends_transcript else "")

    def ask(self, section: Section, before: list[dict[str, Any]], heard: str, fix: str = "") -> Turn:
        runner = self.new_runner()
        try:
            parts = _section_parts(section, len(self.sections), self.total, self.question, before, heard, self.sound,
                                   fix, self.session.title)
            return self.client.run_turn(self.session,
                                        [*self.session.system_messages, {"role": "user", "content": parts}],
                                        runner.run, self.on_event, self.routing, max_tool_rounds=self.max_tool_rounds,
                                        on_delta=self.on_delta)
        finally:
            runner.cleanup_files()

    def describe(self, position: int) -> None:
        """Describes the section at position and stores its description, unchecked."""
        section = self.sections[position]
        number = section.index + 1
        span = f"{format_time(round(section.start))} to {format_time(round(section.end))}"
        self.notify("section", f"Section {number} of {len(self.sections)}, {span}")
        turn = self.ask(section, self.described[:position], self.heard(section))
        if turn.completion.cut_off or not turn.completion.answer.strip():
            # Not stored: a description cut off at the token limit is no description, and sending the first
            # question again continues with this section.
            self.notify("section", f"Section {number} was {'cut off' if turn.completion.cut_off else 'empty'}")
            raise AnswerCutOff(f"The description of section {number} was "
                                f"{'cut off at the token limit' if turn.completion.cut_off else 'empty'}; "
                                "the sections before it are kept. Send the first question again to continue.")
        self.described.append({"index": section.index, "start": section.start, "end": section.end,
                               "text": without_part_heading(turn.completion.answer),
                               "thinking": turn.entries[-1].get("thinking", ""),
                               "usage": turn.completion.usage_record()})
        self.save()

    def anew(self, position: int, fix: str) -> tuple[str, str]:
        """Has the section at position described anew with its video, told what was wrong, and cleans the result.
        Returns the outcome, "rewritten", "fix_failed", "fix_worse" or "fix_unchanged", and the description to
        keep."""
        section, entry = self.sections[position], self.described[position]
        heard = self.heard(section)
        turn = self.ask(section, self.described[:position], heard, fix)
        entry["usage"] = added_usage(entry.get("usage"), turn.completion.usage_record())
        written = without_part_heading(turn.completion.answer)
        if turn.completion.cut_off or not written.strip():
            return "fix_failed", entry["text"]
        # Compared with the first description as the model wrote it, on both ways here: after a check the entry
        # still holds it, after the merge only its review does.
        before = (entry.get("review") or {}).get("first_text") or entry["text"]
        cleaned = clean_description(written, heard)[0]
        if not cleaned.strip() or len(mechanical_findings(written, heard, section.start, section.end)) > len(
                mechanical_findings(before, heard, section.start, section.end)):
            # Worse than what it had by what can be counted, or nothing left once cleaned: that stays.
            return "fix_worse", entry["text"]
        if cleaned == clean_description(entry["text"], heard)[0]:
            # Gemma 4 12B once sent the same description back, ignoring what was found.
            return "fix_unchanged", entry["text"]
        entry["thinking"] = turn.entries[-1].get("thinking", "")
        return "rewritten", cleaned

    def check(self, position: int) -> None:
        """Cleans the section's description mechanically, has it checked, removes what adds nothing, has a
        contradiction described anew once, and stores the result with what the check found."""
        section, entry = self.sections[position], self.described[position]
        number = section.index + 1
        heard = self.heard(section)
        first, first_thinking = entry["text"], entry.get("thinking", "")
        # What needs no model is removed first, mechanically: nothing is replaced, so nothing can be made up.
        cleaned, notes = clean_description(first, heard)
        # The check has no video and no chat, so it judges only the text against the title and what is said.
        self.notify("section", f"Checking section {number}")
        # The same model on the user's own server checks without thinking: with it, Gemma 4 12B went over the
        # same lines again and again, however the prompt asked it to stop (user's wish, 18.11.2026). Its answer
        # is forced into the JSON form, so its reasoning never reads as findings.
        reviewer = self.reviewer
        check_routing = {**((reviewer.routing if reviewer else self.routing) or {}), **REVIEW_FORMAT}
        if not reviewer and self.session.provider == LLAMA_CPP:
            check_routing["chat_template_kwargs"] = {"enable_thinking": False}
        check = (reviewer.client if reviewer else self.client).run_turn(
            replace(self.session, tools=[], **({"model": reviewer.model} if reviewer else {})),
            review_messages(cleaned, heard if self.session.sends_transcript else None, number, section.start,
                            section.end, self.session.title),
            lambda *_: None, self.on_event, check_routing, max_tool_rounds=0, on_delta=self.on_delta)
        entry["usage"] = added_usage(entry.get("usage"), check.completion.usage_record())
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
            self.notify("section", f"The check of section {number} was {failed}; only the mechanical cleaning counts")
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
            self.notify("section", f"Describing section {number} again: {len(contradictions)} lines contradict the "
                                   "title or the transcript")
            entry["text"] = first
            outcome, written = self.anew(position, fix_request(cleaned, contradictions))
            review_record[outcome] = True
            if outcome == "rewritten":
                cleaned = written
        if cleaned != first:
            review_record.update(first_text=first, first_thinking=first_thinking)
        entry["text"] = cleaned
        entry["review"] = review_record
        self.save()

    def merge(self, review: bool) -> Turn:
        """Stores the sections joined without a model, then has them put together into the answer; the merge is
        the last check and may look at a moment again or have a part described anew."""
        described, session = self.described, self.session
        self.progress["joined"] = joined_without_repeats(described)
        self.save()
        self.notify("section", f"Putting the {len(self.sections)} sections together")
        # The merge may look at a moment again and have a part described anew: it is the last check.
        merge_tools = [tool for tool in session.tools if tool.get("type") == "function"
                       and tool["function"]["name"] in (ZOOM_FRAME, HD_FRAME)] + ([REDO_TOOL] if review else [])
        # Parts the merge had described anew before, e.g. before it was cut off, count too: each part once.
        redone = {entry["index"] + 1 for entry in described if (entry.get("review") or {}).get("merge")}
        runner = self.new_runner()

        def run_merge_tool(name: str, arguments: str) -> ToolResult:
            if name != REDO_SECTION:
                return runner.run(name, arguments)
            try:
                asked = json.loads(arguments or "{}")
                number = int(asked["part"])
            except (ValueError, KeyError, TypeError):
                return ToolResult("Give the part's number and what is wrong with its description.")
            if not 1 <= number <= len(self.sections):
                return ToolResult(f"There are parts 1 to {len(self.sections)}.")
            if number in redone:
                return ToolResult(f"Part {number} was described anew already; use what it says now.")
            redone.add(number)
            position, problems = number - 1, str(asked.get("problems", "")).strip()
            self.notify("section", f"Describing section {number} again, as the merge asked: {problems}")
            entry = described[position]
            outcome, written = self.anew(position, MERGE_FIX.format(text=entry["text"], found=problems))
            record = entry.setdefault("review", {})
            record["merge"] = {"problems": problems, "outcome": outcome}
            if outcome == "rewritten":
                record.setdefault("first_text", entry["text"])
                entry["text"] = written
            self.progress["joined"] = joined_without_repeats(described)
            self.save()
            if outcome != "rewritten":
                return ToolResult(f"Part {number} could not be described better; keep its description as it is.")
            return ToolResult(f"Part {number} ({format_time(round(entry['start']))} to "
                              f"{format_time(round(entry['end']))}), described anew:\n\n{written}")

        text = (joined_sections(described)
                + ("\n\n" + MERGE_TRANSCRIPT.format(transcript=session.transcript) if session.sends_transcript else "")
                + ("\n\n" + MERGE_TITLE_HINT.format(title=session.title) if session.title else "")
                + MERGE_TASK.format(total=self.total, question=self.question) + _merge_check(merge_tools))
        try:
            turn = self.client.run_turn(replace(session, tools=merge_tools),
                                        [*session.system_messages, {"role": "user", "content": text}], run_merge_tool,
                                        self.on_event, self.routing,
                                        max_tool_rounds=self.max_tool_rounds if merge_tools else 0,
                                        on_delta=self.on_delta)
        except BaseException:
            runner.cleanup_files()
            raise
        if turn.completion.cut_off or not turn.completion.answer.strip():
            runner.cleanup_files()
            # Every section and check is stored, so sending the first question again only puts them together anew.
            self.notify("section", "Putting the sections together was cut off" if turn.completion.cut_off
                        else "Putting the sections together gave no answer")
            raise AnswerCutOff("Putting the sections together was "
                                f"{'cut off at the token limit' if turn.completion.cut_off else 'empty'}; every "
                                "section is kept. Send the first question again to put them together anew.")
        # The images the merge looked at belong to the answer now, which the chat stores with them.
        runner.commit_files()
        # Cleaned like each section: the answer is what the blind person reads, and the merge, given the whole
        # transcript and asked to keep every detail, can bring back what was said ("Sie betont, wie griffig er
        # ist"). It also loses "Here is the description of the video:", which it wrote before the first time each
        # run.
        answer, notes = clean_description(turn.completion.answer,
                                          session.transcript if session.sends_transcript else "")
        turn.entries[-1]["content"] = answer
        self.progress["answer_notes"] = "\n".join(notes)
        self.save()
        # The usage line under the answer counts every request it took, the sections' included.
        usages = [entry.get("usage") or {} for entry in described]
        completion = replace(turn.completion, answer=answer,
                             requests=turn.completion.requests + sum(int(u.get("requests") or 1) for u in usages),
                             prompt_tokens=turn.completion.prompt_tokens
                             + sum(int(u.get("prompt_tokens") or 0) for u in usages))
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
        parts.append({"type": "text", "text": SECTIONS_BEFORE + joined_sections(before)})
    if heard:
        parts.append({"type": "text", "text": SECTION_TRANSCRIPT_INTRO + heard})
    if fix:
        parts.append({"type": "text", "text": fix.strip()})
    if title:
        parts.append({"type": "text", "text": TITLE_HINT.format(title=title)})
    parts.append({"type": "text", "text": SECTION_TASK.format(
        number=section.index + 1, count=count, span=f"{start} to {end}", total=total, start=start, end=end,
        question=question).strip()})
    return parts + (sound(section) if sound else [])
