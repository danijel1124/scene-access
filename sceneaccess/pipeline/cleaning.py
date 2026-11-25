"""What needs no model in a description, done mechanically: it removes rather than rewrites. A line or sentence
that only repeats what is said or says what someone talks about is deleted, a clause such as "while she talks about
the lid" is cut, text before the first time is dropped and tenths of seconds are rounded off; and what a description
gets wrong that can be counted, for comparing two. Also the sections joined without a model. Asking a model to
replace what was removed made it invent ("she shows how she spreads butter", which she only announces) or state the
obvious ("she looks at the laundry basket"; 18.11.2026)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from ..timecodes import format_time

# A time at the start of a line, with the end of a span after it, as in "**00:00 - 00:25:**" or "1:17 – Ende".
TIME_AT_START = re.compile(r"^\W*(\d{1,2}):(\d{2})(?::(\d{2}))?(\.\d+)?"
                           r"(?:\s*[-–—]\s*(?:\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?|(?i:ende|end))\b)?\W*")
# Where a sentence ends: a description of the whole video puts several in one line.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# The indentation and list marker before a line's text, e.g. "    *   " or "2. ".
LIST_MARKER = re.compile(r"\s*(?:[-*•]|\d+[.)])?\s*")
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
                    r"(says|explains?|tells?)( us)? (that|how|why)|"
                    # With a person before it, the word is speech: "She explains it allows for scooping" (Qwen3.5).
                    r"(sie|er) (sagt|erklärt)|(she|he|they) (say|says|explain|explains|tell|tells))\b",
                    re.IGNORECASE)
# A clause about the speech hanging on a line about what is seen: ", während sie über den Deckel spricht".
SPEECH_CLAUSE = re.compile(r",\s*(während|wobei|indem|als|while|as)\b", re.IGNORECASE)
# A line at the start of a section this alike to one at the end of the section before says the same again.
REPEATED_SIMILARITY = 0.85


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


def _lead_in(line: str) -> bool:
    """Whether the first line of a description of the whole video only announces it, as "Here is an audio
    description of the video "Mein Einarmiger Alltag":" (Qwen3.5-9B, 25.11.2026): no time, no sentence, a colon at
    its end, and it speaks of the description itself. An overview also ends with a colon before a list, as "Die
    Frau mit der Prothese am linken Arm zeigt Alltagsgegenstände, die ihr das Leben erleichtern:", and stays."""
    line = line.strip()
    return (line.endswith(":") and not TIME_AT_START.match(line) and not re.search(r"[.!?](\s|$)", line)
            and bool(re.search(r"(?i)description|beschreibung|audiodeskription", line)))


def _without_speech(sentence: str, spoken: list[str]) -> str:
    """A sentence of a description with what is said cut off, or "" if nothing seen is left: "Sie dreht den
    Deckel auf, während sie über das Drehen spricht." keeps what comes before the clause, as does "…, noting
    that sharp edges would dig in." and "She gestures with her single arm, smiling as she begins to speak about …"
    (Qwen3.5-35B, 19.11.2026); "Sie erwähnt den Staubsauger." goes."""
    if _retold(_content(sentence), spoken):
        return ""
    speech = SPEECH.search(sentence)
    if not speech:
        return sentence
    cuts = [found.start() for found in SPEECH_CLAUSE.finditer(sentence) if found.start() < speech.start()]
    comma = sentence.rfind(",", 0, speech.start())
    if comma >= 0:
        cuts.append(comma)
    seen = sentence[:max(cuts)].strip() if cuts else ""
    return seen.rstrip(" ,;") + "." if len(seen.split()) >= 2 else ""


def clean_description(text: str, heard: str, whole: bool = False) -> tuple[str, list[str]]:
    """A description with what needs no model removed: text before the first time, tenths of seconds, and in the
    timed lines each sentence that only repeats what is said or says what someone talks about, and such a clause
    after what is seen. A description of the whole video (whole) keeps its overview before the first time, but not a
    first line only announcing it (_lead_in), and its paragraphs, and judges its lines after the first time also when they carry none: models put the time in a
    heading such as "**00:00 – 00:26: Zahnpflege**" and the description below it. Returns the cleaned text and a
    note for each change."""
    spoken = _spoken(heard)
    notes, kept = [], []
    timed_before = False
    lines = (text.strip() if whole else without_preamble(text)).splitlines()
    if whole and lines and _lead_in(lines[0]):
        # The overview stays, but not a line announcing it, which an audio track would read out first.
        notes.append(f"- Removed the lead-in: {lines[0].strip()}")
        lines = lines[1:]
    for line in lines:
        if not line.strip():
            # Each paragraph break once, also where a removed line stood between two.
            if whole and kept and kept[-1]:
                kept.append("")
            continue
        line = line.rstrip() if whole else line.strip()
        time = TIME_AT_START.match(line)
        if time and time.group(4):
            # "00:33.0" becomes "00:33"; the rest of the line stays as written.
            line = line[:time.start(4)] + line[time.end(4):]
            time = TIME_AT_START.match(line)
        if time:
            timed_before = True
            start = time.end()
        elif whole and timed_before:
            # Text under a time, as in a heading: judged like the time's own line; its indentation and list
            # marker stay, so "    *   Sie erwähnt X. Sie hält Y." keeps its "*".
            start = LIST_MARKER.match(line).end()
        else:
            # Only the timed lines of a description are judged; other text, e.g. an answer about a brand, stays.
            kept.append(line)
            continue
        sentences = [sentence for sentence in SENTENCE_END.split(line[start:]) if sentence]
        seen = [cleaned for cleaned in (_without_speech(sentence, spoken) for sentence in sentences) if cleaned]
        if seen == sentences:
            kept.append(line)
        elif seen:
            notes.append(f"- Cut what is said from: {line.strip()}")
            kept.append(line[:start] + " ".join(seen))
        elif _retold(_content(line), spoken):
            notes.append(f"- Removed, it only repeats what is said: {line.strip()}")
        else:
            notes.append(f"- Removed, it says what is said: {line.strip()}")
    return "\n".join(kept).strip(), notes


def time_at_start(time: str) -> float | None:
    """The seconds of the time text starts with, as TIME_AT_START reads it; None if it starts with none."""
    found = TIME_AT_START.match(time.strip())
    return _seconds(found) if found else None


def without_lines_at(text: str, times: list[str]) -> tuple[str, list[str]]:
    """text without its lines at the given times; returns it and the lines removed."""
    gone = {at for at in (time_at_start(time) for time in times) if at is not None}
    kept, removed = [], []
    for line in text.splitlines():
        time = TIME_AT_START.match(line.strip())
        (removed if time and _seconds(time) in gone else kept).append(line)
    return "\n".join(kept), removed


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
