"""A video too long for the model in one piece, described in sections: each section of at most the chat's
section length gets its own request with its own picture and sound, the matching part of the transcript and
everything said about the sections before it; a last request puts the descriptions together into the answer.

Each section's description is stored as soon as it is done, together with what it was made for (question,
model, prompt, section length, transcript mode). A description that stops, because it was cancelled, failed or
the server restarted, continues with the next section; if any of those changed, it starts over."""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .openrouter import DeltaHandler, EventHandler, Turn
from .request_control import RequestControl
from .session_store import SECTION_FILE_PREFIX, TRANSCRIPT_HELP, VideoSession, joined_sections
from .tools import format_time, local_video, run_ffmpeg, video_size_and_duration
from .transcript import sound_parts

# Sections end a little before the limit, as encoding can make a cut a few hundredths of a second longer.
LIMIT_MARGIN = 0.98
TRANSCRIPT_LINE = re.compile(r"^\[(\d+(?::\d{2}){1,2})\]")


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
                "transcript, only help to understand it; do not retell them. "
                "Give every time as mm:ss in the whole video, between {start} and {end}. Mention what goes on "
                "from the part before only if something new happens. Start right away with the first time; do not "
                "repeat this task, the transcript or what you wrote about earlier parts. Answer in the language of "
                "the question: {question}")
MERGE_TASK = ("\n\nYour task: give the one answer for the whole video, which is {total} long, as if you had seen it "
              "at once: put your descriptions of the parts above together in order, keep every detail and every "
              "time, and leave out only what repeats where two parts meet. Do not mention the parts. Answer in the "
              "language of the question: {question}")


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
                         model_name: str = "") -> Turn:
    """Describes each section not yet described, saving the progress after each one, then asks for the answer
    to question for the whole video. new_runner gives a tools.ToolRunner per section; its images are not kept,
    as only the descriptions are stored. sound gives a section's sound for a model that gets it apart from the
    video; model_name see progress_key. Returns the turn of the last request, whose answer answers question and
    whose usage counts all requests."""
    notify = on_event or (lambda _kind, _text: None)
    key = progress_key(session, question, model_name)
    stored = session.sections if session.sections and session.sections.get("key") == key else None
    progress = stored or {"key": key, "sections": []}
    described = progress["sections"]
    # Whole seconds only: the model copies the format, and tenths are noise on a braille display.
    total = format_time(round(duration))
    for section in sections[len(described):]:
        start, end = format_time(round(section.start)), format_time(round(section.end))
        span = f"{start} to {end}"
        notify("section", f"Section {section.index + 1} of {len(sections)}, {span}")
        parts: list[dict[str, Any]] = []
        if described:
            parts.append({"type": "text", "text": "What you wrote about the parts before:\n\n" + joined_sections(described)})
        heard = transcript_between(session.transcript, section.start, section.end) if session.sends_transcript else ""
        if heard:
            parts.append({"type": "text", "text": "An automatic transcript of what is said in this part; speech "
                                                  "recognition may contain errors, times are those of the whole "
                                                  f"video. {TRANSCRIPT_HELP}\n\n" + heard})
        data = base64.b64encode(section.path.read_bytes()).decode("ascii")
        parts.append({"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{data}"}})
        parts += sound(section) if sound else []
        parts.append({"type": "text", "text": SECTION_TASK.format(
            number=section.index + 1, count=len(sections), span=span, total=total, start=start, end=end,
            question=question).strip()})
        runner = new_runner()
        try:
            turn = client.run_turn(session, [*session.system_messages, {"role": "user", "content": parts}],
                                   runner.run, on_event, routing, max_tool_rounds=max_tool_rounds, on_delta=on_delta)
        finally:
            runner.cleanup_files()
        described.append({"index": section.index, "start": section.start, "end": section.end,
                          "text": without_part_heading(turn.completion.answer),
                          "thinking": turn.entries[-1].get("thinking", ""),
                          "usage": turn.completion.usage_record()})
        save(progress)
    notify("section", f"Putting the {len(sections)} sections together")
    text = joined_sections(described) + MERGE_TASK.format(total=total, question=question)
    turn = client.run_turn(replace(session, tools=[]), [*session.system_messages, {"role": "user", "content": text}],
                           lambda *_: None, on_event, routing, max_tool_rounds=0, on_delta=on_delta)
    # The usage line under the answer counts every request it took, the sections' included.
    usages = [entry.get("usage") or {} for entry in described]
    completion = replace(turn.completion, requests=turn.completion.requests + sum(int(u.get("requests") or 1) for u in usages),
                         prompt_tokens=turn.completion.prompt_tokens + sum(int(u.get("prompt_tokens") or 0) for u in usages))
    turn.entries[-1]["usage"] = completion.usage_record()
    return Turn(entries=turn.entries, completion=completion)
