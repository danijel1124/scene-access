"""Checks a section's description before it counts, and joins the sections without a model.

A description is checked twice: first mechanically, for lines that only repeat what is said (nearly word for
word in the transcript) and for times that are missing, out of order, outside the section or not mm:ss; then by
the same model in a request of its own, without the video and without the chat, which finds what is retold in
other words. Only when either check finds something does the section go back to the model with its video, the
problems found and its first description, to be described anew, once."""

from __future__ import annotations

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
                    r"(sagt|erklärt|meint|betont),? (dass|wie|warum)|beschreibt (\w+ ){1,4}als|"
                    r"mention\w*|(talks?|talking|speaks?|speaking) (about|of)|says (she|he|they)|noting (that|it)|"
                    r"(says|explains?|tells?)( us)? (that|how|why))\b", re.IGNORECASE)
# A line at the start of a section this alike to one at the end of the section before says the same again.
REPEATED_SIMILARITY = 0.85
# A reviewer's whole answer when it finds nothing.
NOTHING_FOUND = "OK"
# "no" and "nein" too: the four questions ask for problems, so no means fine; yes is a finding.
NOTHING_LISTED = re.compile(r"^\W*(ok|okay|fine|good|correct|no|nein|passt|in ordnung|none|"
                            r"no (problems?|issues?|violations?|errors?)( found)?|nothing( found)?|"
                            r"keine?( (verstöße|fehler|probleme|beanstandungen))?( gefunden)?|nichts( gefunden)?)"
                            r"\W*$", re.IGNORECASE)
# What a reviewer may put before its verdict on a line: the line's time, or one of the four questions ("a.").
LINE_PREFIX = re.compile(r"^\W*(\d{1,2}:\d{2}(:\d{2})?\W*)?([a-d][.)]\s*)?(\w+( \w+)?\s*[:?]\s*)?", re.IGNORECASE)

# Who the reviewer is and what it does: its own system prompt, as it is a model of its own in this request
# and must not take the describer's prompt for its task (user's wish, 18.11.2026).
REVIEWER_SYSTEM_PROMPT = (
    "You are the reviewer in SceneAccess, an app that describes videos for blind people. Another model watched "
    "a part of a video and described it; you check that description before the blind person gets it. You do not "
    "see the video and do not describe it yourself. You get the description, the time span of the part, the "
    "question the description answers and, if there is one, an automatic transcript of what is said in the part.\n\n"
    "How to check, once and in this order:\n"
    "1. Go through the description line by line, from top to bottom, exactly once.\n"
    "2. For each line, ask only these four questions; for each, yes means a problem and no means fine:\n"
    "   a. Is its time missing, not mm:ss, outside the part, or not later than the line before?\n"
    "   b. Does it say anything about the speech: what is said, quoted, mentioned, explained or talked about, "
    "also as \"while she talks about …\"? The blind person hears the speech, so any of it is a problem.\n"
    "   c. Is it in another language than the question?\n"
    "   d. Is a word misspelt, e.g. a digit inside a word?\n"
    "3. Decide each question once. Note the line only if one of the questions finds a problem. If you are unsure, "
    "the line is fine.\n"
    "4. When you reach the last line, the check is over: write your answer at once.\n\n"
    "What you have decided is checked and closed: never think about a line or a question again once you have "
    "decided it, and never go back up the list, not even to ask whether there are other problems. A problem you "
    "found is noted and done; finding it again adds nothing.\n\n"
    "Over-analysing is the mistake to avoid: one pass is enough, and checking longer does not make the check "
    "better. Do not sort the lines into kinds such as actions or details. Do not judge whether what is described is true, as you cannot see it, and "
    "do not rewrite the description.\n\n"
    "Your answer: every line with a problem, one per line starting with \"- \", with the time of the line and the "
    "question it fails. If no line has a problem, answer only " + NOTHING_FOUND + ".")
REVIEW_TASK = ("Part {number} of the video, from {start} to {end}. The question: {question}\n\n"
               "The description:\n\n{text}\n\nThe transcript of this part:\n\n{heard}")
FIX_TASK = ("\n\nYour first description of this part:\n\n{text}\n\nA check found these problems:\n\n{found}\n\n"
            "Write the description of this part anew and fix them: where you repeated what is said, describe what "
            "can be seen at that time instead. Write the corrected description once, without drafts.")


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


def mechanical_findings(text: str, heard: str, start: float, end: float) -> list[str]:
    """What a description gets wrong that needs no model to see, each as a line for the model to fix."""
    found = []
    spoken = [_content(TRANSCRIPT_TEXT.sub("", line)) for line in heard.splitlines() if line.strip()]
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
        if len(content.split()) >= 4 and any(_alike(content, words) >= RETOLD_SIMILARITY or
                                             (len(content) <= len(words) * 1.3 and content in words)
                                             for words in spoken):
            found.append(f"- {line}: this only repeats what is said; describe what can be seen instead.")
        elif SPEECH.search(TIME_AT_START.sub("", line)):
            # The blind person hears the speech, so saying what someone talks about adds nothing (user's wish).
            found.append(f"- {line}: this says what is said, which the blind person hears; describe what can be "
                         "seen instead, or leave the moment out.")
    if not timed:
        found.append("- The description gives no times; start each line with the time as mm:ss.")
    return found


def review_messages(text: str, heard: str | None, number: int, start: float, end: float,
                    question: str) -> list[dict[str, Any]]:
    """The reviewer's request: its own system prompt, then the part to check. heard is the transcript of the
    part, or None when the chat sends no transcript: then the reviewer must not take speech the description
    mentions for made up."""
    if heard is None:
        heard = "(no transcript is available; the model may have heard the speech itself)"
    part = REVIEW_TASK.format(number=number, start=format_time(round(start)), end=format_time(round(end)),
                              question=question, text=text.strip(),
                              heard=heard.strip() or "(nothing is said in this part)")
    return [{"role": "system", "content": REVIEWER_SYSTEM_PROMPT}, {"role": "user", "content": part}]


def reviewer_findings(answer: str) -> list[str]:
    """The problems a reviewer listed, one per line starting with "- " as asked. An answer without such lines
    found nothing, however it says so: a model answering in the question's language writes "OK, keine Fehler."
    or "**OK**" as often as OK, and taking that for a finding would describe every section twice."""
    listed = [line.strip()[2:].strip() for line in answer.splitlines() if line.strip().startswith(("- ", "* ", "• "))]
    # Nothing found, written as a list line: "- Keine Verstöße.", "- None.", also after the line's time or one of
    # the four questions, as a model going through them writes "- 00:13: OK" or "* a. Time: Yes". A finding
    # such as "- Keine Zeitangabe bei 00:13" names what is missing, so only such a word alone counts as nothing.
    return [f"- {line}" for line in listed
            if line and not NOTHING_LISTED.match(line) and not NOTHING_LISTED.match(LINE_PREFIX.sub("", line, count=1))]


def fix_request(text: str, found: list[str]) -> str:
    return FIX_TASK.format(text=text.strip(), found="\n".join(found))


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
