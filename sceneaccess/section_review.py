"""Checks a section's description before it counts, and joins the sections without a model.

What needs no model is done mechanically and removes rather than rewrites: a line that only repeats what is
said or says what someone talks about is deleted, a clause such as "while she talks about the lid" is cut, text
before the first time is dropped and tenths of seconds are rounded off. The same model then checks the rest in a
request of its own, without the video and without the chat, for only what a model can see: a line that
contradicts the title or the transcript, and a line that only says what a blind listener would guess anyway. The
second kind is deleted; only a contradiction sends the section back to the model with its video, once. Asking
a model to replace what was removed made it invent ("she shows how she spreads butter", which she only
announces) or state the obvious ("she looks at the laundry basket"; 18.11.2026)."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from .tools import format_time

TIME_AT_START = re.compile(r"^\W*(\d{1,2}):(\d{2})(?::(\d{2}))?(\.\d+)?\W*")
TRANSCRIPT_TEXT = re.compile(r"^\[[\d:]+\]\s*")
# How alike a description line and a transcript line must be, word by word, to count as repeated. Measured on
# Gemma 4 12B's real sections (18.11.2026): transcript lines copied into the description 1.00, "Sie erwähnt,
# dass die Klopapierrolle auf der linken Seite sein muss." 0.74, while lines about what is seen, such as "Sie
# zeigt ihr spezielles Schneidebrett.", stayed below 0.5. What is retold in other words or another language
# scores near 0; the reviewer finds that.
RETOLD_SIMILARITY = 0.7
# Words that almost always mean speech, in German and English, e.g. "Sie erwähnt", "während sie über den Deckel
# spricht", "She mentions". Words that also fit what is seen stay with the reviewer, which sees the context:
# "says" (a sign that says "Open"), "betont" (the make-up stresses her eyes), "spricht in die Kamera".
# Measured on every line the Gemma runs wrote (18.11.2026): no line about what is seen was reported.
SPEECH = re.compile(r"\b(erwähn\w*|erläuter\w*|(spricht|sprechen|redet|reden) (über|von)|"
                    # In a German subordinate clause the verb comes last: "während sie über das „Drehen“ spricht".
                    r"(über|von) ([\w„“\"'»«-]+ ){1,4}(spricht|sprechen|redet|reden)|erzähl\w*|"
                    r"(sagt|erklärt|meint|betont|präzisiert),? (dass|wie|warum)|beschreibt (\w+ ){1,4}als|"
                    r"mention\w*|(talks?|talking|speaks?|speaking) (about|of)|says (she|he|they)|noting (that|it)|"
                    r"(says|explains?|tells?)( us)? (that|how|why))\b", re.IGNORECASE)
# A clause about the speech hanging on a line about what is seen: ", während sie über den Deckel spricht".
SPEECH_CLAUSE = re.compile(r",\s*(während|wobei|indem|als|while|as)\b", re.IGNORECASE)
# A line at the start of a section this alike to one at the end of the section before says the same again.
REPEATED_SIMILARITY = 0.85

# Who the reviewer is and what it does: its own system prompt, as it is a model of its own in this request
# and must not take the describer's prompt for its task (user's wish, 18.11.2026). Only what needs a model is
# asked; everything else the mechanical check does better.
REVIEWER_SYSTEM_PROMPT = (
    "You are the reviewer in SceneAccess, an app that describes videos for blind people. Another model watched "
    "a part of a video and described it; you check that description before the blind person gets it. You do not "
    "see the video and do not describe it yourself. You get the description, the time span of the part, the "
    "video's title and, if there is one, an automatic transcript of what is said in the part.\n\n"
    "Go through the description line by line, once, and ask only these two questions:\n"
    "e. Does the line clearly contradict the title or the transcript, e.g. a person sitting still while the "
    "transcript says she is running, or a snowy street in a video titled \"A day at the beach\"?\n"
    "f. Does the line only say what the blind person would guess from what they hear, e.g. that someone looks "
    "at, points to or holds what they are talking about? Such a line adds nothing for them.\n\n"
    "Decide each line once and never go back. If you are unsure, the line is fine. Apart from these two "
    "questions, do not judge whether what is described is true, as you cannot see it, and do not rewrite it.\n\n"
    "Answer only with the JSON asked for: every line with a problem, with its time as mm:ss, the question it "
    "fails (e or f) and why in a few words; an empty list if no line has a problem.")
# The answer is forced into this form by the server's grammar, so the reviewer's reasoning never reads as
# findings (Gemma 4 12B without thinking wrote its reasoning as a list and ended with "Result: OK").
REVIEW_SCHEMA = {"type": "object", "properties": {"problems": {"type": "array", "items": {
    "type": "object", "properties": {"time": {"type": "string"}, "question": {"type": "string", "enum": ["e", "f"]},
                                     "why": {"type": "string"}},
    "required": ["time", "question", "why"]}}}, "required": ["problems"]}
REVIEW_FORMAT = {"response_format": {"type": "json_schema", "json_schema": {"name": "findings", "schema": REVIEW_SCHEMA}}}
REVIEW_TASK = ("Part {number} of the video, from {start} to {end}.\nThe video's title: {title}\n\n"
               "The description:\n\n{text}\n\nThe transcript of this part:\n\n{heard}")
FIX_TASK = ("\n\nYour first description of this part:\n\n{text}\n\nA check found that these lines contradict the "
            "title or the transcript:\n\n{found}\n\nLook at the part again and write its description anew. Correct "
            "those lines from what you see; where you cannot see what is there, leave the line out. Add nothing that "
            "is not in the picture, and write the description once, without drafts.")


def _content(line: str) -> str:
    """A line without its leading time, in lower case, without punctuation and with single spaces, for comparing:
    quoting what is said with quotation marks still matches it."""
    return " ".join(re.sub(r"[^\w\s]", " ", TIME_AT_START.sub("", line).lower()).split())


def _seconds(found: re.Match) -> float:
    first, second, third = found.group(1), found.group(2), found.group(3)
    return int(first) * 3600 + int(second) * 60 + int(third) if third else int(first) * 60 + int(second)


def _alike(one: str, other: str) -> float:
    """How alike two compared lines are, word by word: letter by letter, short lines such as "part 1" and
    "part 2" would count as the same."""
    return SequenceMatcher(None, one.split(), other.split()).ratio() if one and other else 0.0


def _retold(content: str, spoken: list[str]) -> bool:
    return len(content.split()) >= 4 and any(_alike(content, words) >= RETOLD_SIMILARITY or
                                             (len(content) <= len(words) * 1.3 and content in words)
                                             for words in spoken)


def _spoken(heard: str) -> list[str]:
    return [_content(TRANSCRIPT_TEXT.sub("", line)) for line in heard.splitlines() if line.strip()]


def mechanical_findings(text: str, heard: str, start: float, end: float) -> list[str]:
    """What a description gets wrong that needs no model to see, each as a line; used to compare a description
    with one written anew."""
    found = []
    spoken = _spoken(heard)
    last = -1.0
    timed = 0
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        time = TIME_AT_START.match(line)
        content = _content(line)
        if time:
            timed += 1
            at = _seconds(time)
            if time.group(4):
                found.append(f"- {line[:time.end()].strip()}: give times as mm:ss without tenths.")
            if not start - 1 <= at <= end + 1:
                found.append(f"- {format_time(at)}: this time is outside the part, {format_time(round(start))} to "
                             f"{format_time(round(end))}.")
            elif at < last:
                found.append(f"- {format_time(at)}: the times are out of order.")
            else:
                # Only a time within the part sets the order; one outside it is reported already.
                last = at
        if _retold(content, spoken):
            found.append(f"- {line}: this only repeats what is said; describe what can be seen instead.")
        elif SPEECH.search(TIME_AT_START.sub("", line)):
            # The blind person hears the speech, so saying what someone talks about adds nothing (user's wish).
            found.append(f"- {line}: this says what is said, which the blind person hears; describe what can be "
                         "seen instead, or leave the moment out.")
    if not timed:
        found.append("- The description gives no times; start each line with the time as mm:ss.")
    return found


def without_preamble(text: str) -> str:
    """text from its first line with a time: models write "Hier ist die Beschreibung des Videos:" before it,
    although asked to start with the first time. Text without any time stays as it is."""
    lines = text.strip().splitlines()
    first = next((index for index, line in enumerate(lines) if TIME_AT_START.match(line.strip())), None)
    return "\n".join(lines[first:]).strip() if first else text.strip()


def clean_description(text: str, heard: str) -> tuple[str, list[str]]:
    """A description with what needs no model removed: text before the first time, tenths of seconds, lines that
    only repeat what is said or say what someone talks about, and such a clause after what is seen. Returns the
    cleaned text and a note for each change."""
    spoken = _spoken(heard)
    notes, kept = [], []
    for line in without_preamble(text).splitlines():
        line = line.strip()
        if not line:
            continue
        time = TIME_AT_START.match(line)
        if time and time.group(4):
            # "00:33.0" becomes "00:33"; the rest of the line stays as written.
            line = line[:time.start(4)] + line[time.end(4):]
            time = TIME_AT_START.match(line)
        body = line[time.end():] if time else line
        if _retold(_content(line), spoken):
            notes.append(f"- Removed, it only repeats what is said: {line}")
            continue
        speech = SPEECH.search(body)
        if speech:
            clause = [found for found in SPEECH_CLAUSE.finditer(body) if found.start() < speech.start()]
            seen = body[:clause[-1].start()].strip() if clause else ""
            if len(seen.split()) >= 2:
                cut = line[:time.end()] + seen.rstrip(" ,;") + "." if time else seen.rstrip(" ,;") + "."
                notes.append(f"- Cut what is said from: {line}")
                kept.append(cut)
            else:
                notes.append(f"- Removed, it says what is said: {line}")
            continue
        kept.append(line)
    return "\n".join(kept), notes


def review_messages(text: str, heard: str | None, number: int, start: float, end: float,
                    title: str = "") -> list[dict[str, Any]]:
    """The reviewer's request: its own system prompt, then the part to check. heard is the transcript of the
    part, or None when the chat sends no transcript: then the reviewer must not take speech the description
    mentions for made up."""
    if heard is None:
        heard = "(no transcript is available; the model may have heard the speech itself)"
    part = REVIEW_TASK.format(number=number, start=format_time(round(start)), end=format_time(round(end)),
                              title=title or "(none)", text=text.strip(),
                              heard=heard.strip() or "(nothing is said in this part)")
    return [{"role": "system", "content": REVIEWER_SYSTEM_PROMPT}, {"role": "user", "content": part}]


def reviewer_findings(answer: str) -> list[dict[str, str]]:
    """The problems in a reviewer's JSON answer, each with time, question ("e" or "f") and why. Raises
    ValueError for an answer that is not the JSON asked for, so it never counts as nothing found."""
    try:
        problems = json.loads(answer)["problems"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"The reviewer's answer is not the JSON asked for: {answer[:200]}") from error
    found = []
    for problem in problems if isinstance(problems, list) else []:
        if isinstance(problem, dict) and problem.get("question") in ("e", "f") and TIME_AT_START.match(
                str(problem.get("time", ""))):
            found.append({"time": str(problem["time"]).strip(), "question": problem["question"],
                          "why": str(problem.get("why", "")).strip()})
    return found


def _at(time: str) -> float | None:
    found = TIME_AT_START.match(time.strip())
    return _seconds(found) if found else None


def without_lines_at(text: str, times: list[str]) -> tuple[str, list[str]]:
    """text without its lines at the given times; returns it and the lines removed."""
    gone = {at for at in (_at(time) for time in times) if at is not None}
    kept, removed = [], []
    for line in text.splitlines():
        time = TIME_AT_START.match(line.strip())
        (removed if time and _seconds(time) in gone else kept).append(line)
    return "\n".join(kept), removed


def fix_request(text: str, contradictions: list[dict[str, str]]) -> str:
    found = "\n".join(f"- {problem['time']}: {problem['why']}" for problem in contradictions)
    return FIX_TASK.format(text=text.strip(), found=found)


def joined_without_repeats(described: list[dict[str, Any]]) -> str:
    """The sections' descriptions one after the other, without a model: a line at the start of a section that says
    what the last lines of the section before already said is left out, as each section sees where the one before
    stopped."""
    lines: list[str] = []
    for entry in described:
        own = [line.strip() for line in entry["text"].strip().splitlines() if line.strip()]
        before = [_content(line) for line in lines[-2:]]
        # Only the first two lines: further on, a line alike to one before is something that happened again.
        for _ in range(2):
            if own and any(_alike(_content(own[0]), earlier) >= REPEATED_SIMILARITY for earlier in before):
                own.pop(0)
        lines += own
    return "\n".join(lines)
