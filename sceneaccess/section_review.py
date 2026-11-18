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
# How alike a description line and a transcript line must be to count as repeated: wording such as
# "Sie sagt: So und dann mach ich das drauf." against "So und dann mach ich das drauf." scores far above it,
# a description of what is seen far below.
RETOLD_SIMILARITY = 0.7
# A line at the start of a section this alike to one at the end of the section before says the same again.
REPEATED_SIMILARITY = 0.85
# A reviewer's whole answer when it finds nothing.
NOTHING_FOUND = "OK"

REVIEW_TASK = (
    "You check a description of part {number} of a video for a blind person; the part runs from {start} to {end}. "
    "A model that saw the video wrote it. You do not see the video; below the description is an automatic "
    "transcript of what is said in this part.\n\nThe rules: the description says what can be seen, the actions, "
    "aids, objects and details; it does not retell or quote what is said, as the person hears that anyway. Every "
    "line starts with a time as mm:ss between {start} and {end}, in order. It is in the language of this "
    "question: {question}\n\nList every line that breaks a rule, one per line starting with \"- \", with the time "
    "of the line and the rule it breaks. If no line breaks a rule, answer only {ok}.\n\nThe description:\n\n{text}"
    "\n\nThe transcript of this part:\n\n{heard}")
FIX_TASK = ("\n\nYour first description of this part:\n\n{text}\n\nA check found these problems:\n\n{found}\n\n"
            "Write the description of this part anew and fix them: where you repeated what is said, describe what "
            "can be seen at that time instead.")


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
        # Quoting what is said is fine when it explains what is seen; a line that is the speech and little else
        # is not.
        if len(content.split()) >= 4 and any(_alike(content, words) >= RETOLD_SIMILARITY or
                                             (len(content) <= len(words) * 1.3 and content in words)
                                             for words in spoken):
            found.append(f"- {line}: this only repeats what is said; describe what can be seen instead.")
    if not timed:
        found.append("- The description gives no times; start each line with the time as mm:ss.")
    return found


def review_request(text: str, heard: str, number: int, start: float, end: float, question: str) -> str:
    return REVIEW_TASK.format(number=number, start=format_time(round(start)), end=format_time(round(end)),
                              question=question, ok=NOTHING_FOUND, text=text.strip(),
                              heard=heard.strip() or "(nothing is said in this part)")


def reviewer_findings(answer: str) -> list[str]:
    """The problems a reviewer listed; none when it answered that it found nothing."""
    answer = answer.strip()
    if not answer or answer.rstrip(".!").upper() == NOTHING_FOUND:
        return []
    listed = [line.strip() for line in answer.splitlines() if line.strip().startswith(("-", "*", "•"))]
    return [f"- {line.lstrip('-*• ').strip()}" for line in listed] or [f"- {answer}"]


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
