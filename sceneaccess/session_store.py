from __future__ import annotations

import base64
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .prompts import sound_blocks
from .providers import OPENROUTER


class SessionNotFound(KeyError):
    """The requested private session does not exist for this owner."""


# Fields of a stored message that belong to the chat protocol. Everything else (usage, sources) is local.
WIRE_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name", "reasoning_details")
# Images the model asked for are stored as files in the session folder and referenced like this.
SESSION_FILE_PREFIX = "session-file:"

COLUMNS = ("id", "owner_id", "title", "source_path", "model", "initial_question", "history_json",
           "system_prompt", "tools_json", "source_url", "video_info_json", "transcript", "transcript_state", "audio_only",
           "size_confirmed", "provider", "transcribe")
# Columns added after the first release, with the value older chats get. Older chats get no transcript,
# so opening them does not change what is sent and their cache stays valid. Chats from before llama.cpp
# support were all answered by OpenRouter, and older chats transcribe as needed. Databases from before the
# transcript mode may still hold an unused openrouter_transcript column.
ADDED_COLUMNS = (("system_prompt", "''"), ("tools_json", "'[]'"), ("source_url", "''"), ("video_info_json", "''"),
                 ("transcript", "''"), ("transcript_state", "'none'"), ("audio_only", "'0'"), ("size_confirmed", "'0'"),
                 ("provider", f"'{OPENROUTER}'"), ("transcribe", "'auto'"))

# transcribe: "auto" makes and sends a transcript only while the chat's model cannot hear the video, "on" always,
# "off" never. Making one sends the sound to OpenRouter's speech recognition.
TRANSCRIBE_AUTO, TRANSCRIBE_ON, TRANSCRIBE_OFF = "auto", "on", "off"
TRANSCRIBE_MODES = (TRANSCRIBE_AUTO, TRANSCRIBE_ON, TRANSCRIBE_OFF)
TRANSCRIBE_LABELS = {TRANSCRIBE_AUTO: "Auto: only while the model cannot hear the video",
                     TRANSCRIBE_ON: "On: always, also for models that hear",
                     TRANSCRIBE_OFF: "Off: never; the sound is not sent"}
TRANSCRIBE_HEADING = "Transcript of the speech (through OpenRouter and ElevenLabs; needs an OpenRouter API key)"

# transcript_state: "pending" until the first request decides whether the model needs a transcript,
# "none" if the chat's transcript mode asked for none (each question decides again), "done" (text may be empty: no speech), "failed", or "appended": added
# later at the end of the chat as a history entry, so the cached video and history before it stay valid.
TRANSCRIPT_PENDING, TRANSCRIPT_NONE, TRANSCRIPT_DONE, TRANSCRIPT_FAILED, TRANSCRIPT_APPENDED = (
    "pending", "none", "done", "failed", "appended")
TRANSCRIPT_INTRO = ("You cannot hear this video, so here is an automatic transcript of its speech. Speech "
                    "recognition may contain errors; times are mm:ss from the start of the video.\n\n")
TRANSCRIPT_LATER_INTRO = ("Here is an automatic transcript of the video's speech, added after the video. Speech "
                          "recognition may contain errors; times are mm:ss from the start of the video.\n\n")
# A stored entry marked like this stays in the Chat history but is not sent to the model.
EXCLUDED = "excluded"
# Marks the history entry of a transcript added later; it is sent as a user message.
TRANSCRIPT_ENTRY = "transcript"
# The video's sound kept beside it for a llama.cpp server, in pieces <prefix>000.mp3, ...; the other file marks
# a video without sound. Both belong to the stored video and go when it is replaced.
SOUND_FILE_PREFIX, NO_SOUND_FILE = "context-sound-", "context-sound.none"


def is_question(message: dict[str, Any]) -> bool:
    """A follow-up question; images the model asked for are user messages too, with a list as content."""
    return message.get("role") == "user" and isinstance(message.get("content"), str)


def is_answer(message: dict[str, Any]) -> bool:
    return message.get("role") == "assistant" and not message.get("tool_calls")


def round_bounds(history: list[dict[str, Any]], index: int) -> tuple[int, int]:
    """The first and last history index of the round history[index] belongs to: from its question (or the
    chat's start for the first round) through tool calls, results and images to its answer."""
    start = index
    while start > 0 and not is_question(history[start]):
        start -= 1
    end = index
    while end < len(history) - 1 and not is_answer(history[end]) and not is_question(history[end + 1]):
        end += 1
    return start, end


def _round_positions(history: list[dict[str, Any]], indices: list[int]) -> set[int]:
    """Every history position in the rounds of the entries at indices; indices past the end are ignored."""
    positions: set[int] = set()
    for index in indices:
        if 0 <= index < len(history):
            start, end = round_bounds(history, index)
            positions.update(range(start, end + 1))
    return positions


def _merge_user_runs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Joins user messages that follow each other, e.g. after the first answer was taken out of the context:
    some chat templates, such as Gemma's and Mistral's, refuse roles that do not alternate."""
    merged: list[dict[str, Any]] = []
    for message in messages:
        if merged and message.get("role") == "user" and merged[-1].get("role") == "user":
            parts = [part for item in (merged[-1], message) for part in _content_parts(item["content"])]
            merged[-1] = {**merged[-1], "content": parts}
        else:
            merged.append(message)
    return merged


def _content_parts(content: Any) -> list[dict[str, Any]]:
    return list(content) if isinstance(content, list) else [{"type": "text", "text": str(content)}]


@dataclass
class VideoSession:
    id: str
    owner_id: str
    title: str
    source_path: str
    # "" follows the model in Settings ("default"); a model ID is the chat's own choice.
    model: str
    initial_question: str
    history: list[dict[str, Any]]
    data_url_path: Path
    # Frozen when the chat starts: they precede the video in every request, so changing them would
    # rewrite the video's cache entry, and later edits in Settings must not change an existing chat.
    system_prompt: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    source_url: str = ""
    video_info: dict[str, Any] | None = None
    # Lines like "[01:23] What was said", for models that cannot hear the video's sound.
    transcript: str = ""
    transcript_state: str = TRANSCRIPT_NONE
    # With openrouter/auto, route only to models that hear the video's sound.
    audio_only: bool = False
    # A question with the too large stored video was answered, so the chat stops asking about its size
    # until its video or its Chat settings change.
    size_confirmed: bool = False
    # Fixed when the chat starts, so its video never goes to another provider than the one chosen for it.
    provider: str = OPENROUTER
    # When the chat makes a transcript and sends it to the model (see TRANSCRIBE_MODES), chosen per chat.
    transcribe: str = TRANSCRIBE_AUTO
    # Not stored: whether the model answering now hears the video's sound, set before each request.
    hears_sound: bool = False

    @property
    def directory(self) -> Path:
        return self.data_url_path.parent

    @property
    def source_missing(self) -> bool:
        return not Path(self.source_path).is_file()

    @property
    def video_data_url(self) -> str:
        return self.data_url_path.read_text(encoding="ascii")

    @property
    def sent_video_size(self) -> int:
        """Bytes the stored video takes in every request: its Base64 data URL."""
        return self.data_url_path.stat().st_size

    @property
    def messages(self) -> list[dict[str, Any]]:
        # OpenRouter reads explicit cache breakpoints from content parts, not from the message. The blocks about
        # the sound go only to a model that takes it in, by hearing or from the transcript.
        # A failed or skipped transcript keeps its reason in the same field; that is no transcript. A transcript
        # the chat's mode leaves out stays stored, so switching back never pays for it again.
        wanted = self.transcribe == TRANSCRIBE_ON or (self.transcribe == TRANSCRIBE_AUTO and not self.hears_sound)
        has_transcript = wanted and self.transcript_state == TRANSCRIPT_DONE and bool(self.transcript)
        added_later = any(entry.get(TRANSCRIPT_ENTRY) and not entry.get(EXCLUDED) for entry in self.history)
        blocks = sound_blocks(gets_speech=self.hears_sound or has_transcript or added_later, hears=self.hears_sound)
        prompt = f"{self.system_prompt}\n\n{blocks}" if self.system_prompt and blocks else self.system_prompt
        system = [{"role": "system", "content": prompt}] if prompt else []
        # The transcript comes before the video's cache breakpoint, so it is cached together with the video.
        transcript = [{"type": "text", "text": TRANSCRIPT_INTRO + self.transcript}] if has_transcript else []
        return _merge_user_runs([*system, {
            "role": "user",
            "content": [
                {"type": "text", "text": self.initial_question},
                *transcript,
                {"type": "video_url", "video_url": {"url": self.video_data_url}, "cache_control": {"type": "ephemeral"}},
            ],
        }, *(self.wire_message(message) for message in self.history if not message.get(EXCLUDED))])

    def wire_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Returns a stored message as sent to the provider: protocol fields only, images inlined."""
        wire = {key: message[key] for key in WIRE_KEYS if key in message}
        if isinstance(wire.get("content"), list):
            wire["content"] = [self._inline_image(part) for part in wire["content"]]
        return wire

    def _inline_image(self, part: dict[str, Any]) -> dict[str, Any]:
        url = (part.get("image_url") or {}).get("url", "") if part.get("type") == "image_url" else ""
        if not url.startswith(SESSION_FILE_PREFIX):
            return part
        data = (self.directory / Path(url[len(SESSION_FILE_PREFIX):]).name).read_bytes()
        return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}}


class VideoSessionStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.sessions_dir, 0o700)
        self.db_path = self.root / "sceneaccess.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, owner_id TEXT, title TEXT, source_path TEXT,
                model TEXT, initial_question TEXT, history_json TEXT)""")
            existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            for column, default in ADDED_COLUMNS:
                if column not in existing:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}")
        os.chmod(self.db_path, 0o600)

    def data_url_path(self, session_id: str) -> Path:
        return self.sessions_dir / session_id / "context.data-url"

    def create_session(self, *, owner_id: str, source_path: Path, title: str, model: str, initial_question: str,
                       system_prompt: str = "", tools: list[dict[str, Any]] | None = None, source_url: str = "",
                       video_info: dict[str, Any] | None = None, transcript_state: str = TRANSCRIPT_NONE,
                       audio_only: bool = False, sent_video: Path | None = None, provider: str = OPENROUTER,
                       transcribe: str = TRANSCRIBE_AUTO) -> VideoSession:
        """sent_video is a smaller copy to send instead of source_path, e.g. shrunk for a provider's size limit."""
        source_path = Path(source_path).resolve()
        sent = Path(sent_video) if sent_video else source_path
        mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/mov"}.get(sent.suffix.lower())
        if not source_path.is_file() or not sent.is_file() or not mime:
            raise FileNotFoundError(sent if source_path.is_file() else source_path)
        # Read the video before creating anything, so a failed read leaves no empty session behind.
        data_url = f"data:{mime};base64," + base64.b64encode(sent.read_bytes()).decode("ascii")
        session_id = "vid_" + uuid.uuid4().hex
        directory = self.sessions_dir / session_id
        values = (session_id, owner_id, title, str(source_path), model, initial_question, "[]", system_prompt,
                  json.dumps(tools or []), source_url, json.dumps(video_info) if video_info else "",
                  "", transcript_state, "1" if audio_only else "0", "0", provider,
                  transcribe)
        # A collision must never authorize cleanup of an existing directory or file.
        directory.mkdir(mode=0o700)
        data_url_path = directory / "context.data-url"
        created_file = False
        try:
            with data_url_path.open("x", encoding="ascii") as context:
                created_file = True
                context.write(data_url)
            os.chmod(data_url_path, 0o600)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(f"INSERT INTO sessions ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", values)
        except BaseException:
            if created_file:
                data_url_path.unlink(missing_ok=True)
            # Do not recursively remove unexpected content, even on failure.
            directory.rmdir()
            raise
        return self.get_session(session_id, owner_id)

    def get_session(self, session_id: str, owner_id: str) -> VideoSession:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM sessions WHERE id = ? AND owner_id = ?",
                               (session_id, owner_id)).fetchone()
        if not row:
            raise SessionNotFound(session_id)
        values = dict(zip(COLUMNS, row))
        return VideoSession(
            values["id"], values["owner_id"], values["title"], values["source_path"], values["model"],
            values["initial_question"], json.loads(values["history_json"]),
            data_url_path=self.data_url_path(session_id),
            system_prompt=values["system_prompt"], tools=json.loads(values["tools_json"]),
            source_url=values["source_url"],
            video_info=json.loads(values["video_info_json"]) if values["video_info_json"] else None,
            transcript=values["transcript"], transcript_state=values["transcript_state"],
            audio_only=values["audio_only"] == "1", size_confirmed=values["size_confirmed"] == "1",
            provider=values["provider"], transcribe=values["transcribe"],
        )

    def list_sessions(self, owner_id: str) -> list[VideoSession]:
        with sqlite3.connect(self.db_path) as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM sessions WHERE owner_id = ? ORDER BY rowid DESC", (owner_id,)
            )]
        return [self.get_session(session_id, owner_id) for session_id in ids]

    def record_turn(self, session_id: str, owner_id: str, entries: list[dict[str, Any]]) -> None:
        """Appends one turn: the question (if any), tool calls, tool results, images and the answer."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT history_json FROM sessions WHERE id = ? AND owner_id = ?",
                               (session_id, owner_id)).fetchone()
            if row is None:
                raise SessionNotFound(session_id)
            history = json.loads(row[0])
            conn.execute("UPDATE sessions SET history_json = ? WHERE id = ? AND owner_id = ?",
                         (json.dumps([*history, *entries]), session_id, owner_id))

    def update_initial_question(self, session_id: str, owner_id: str, question: str) -> None:
        """A first-request retry uses the question actually submitted, not the failed original."""
        session = self.get_session(session_id, owner_id)
        if session.history:
            raise ValueError("The initial question of an answered chat cannot be replaced.")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET initial_question = ? WHERE id = ? AND owner_id = ?",
                         (question, session_id, owner_id))

    def update_source_path(self, session_id: str, owner_id: str, source_path: Path) -> None:
        source_path = Path(source_path).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET source_path = ? WHERE id = ? AND owner_id = ?",
                         (str(source_path), session_id, owner_id))

    def update_setup(self, session_id: str, owner_id: str, system_prompt: str, tools: list[dict[str, Any]],
                     audio_only: bool = False) -> None:
        """Replaces a chat's prompt and tools. The next request caches the video again and, unless the chat has
        a finished or added transcript, which is never paid for twice, decides about a transcript again."""
        session = self.get_session(session_id, owner_id)
        state, transcript = session.transcript_state, session.transcript
        if state not in (TRANSCRIPT_DONE, TRANSCRIPT_APPENDED):
            state, transcript = TRANSCRIPT_PENDING, ""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET system_prompt = ?, tools_json = ?, audio_only = ?, transcript = ?, "
                         "transcript_state = ? WHERE id = ? AND owner_id = ?",
                         (system_prompt, json.dumps(tools), "1" if audio_only else "0", transcript, state,
                          session_id, owner_id))

    def update_system_prompt(self, session_id: str, owner_id: str, system_prompt: str) -> None:
        """Replaces only the chat's system prompt; its tools and transcript stay. The next request caches the
        video again."""
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET system_prompt = ? WHERE id = ? AND owner_id = ?",
                         (system_prompt, session_id, owner_id))

    def update_chat_settings(self, session_id: str, owner_id: str, model: str, audio_only: bool, *,
                             provider: str | None = None, transcribe: str | None = None,
                             tools: list[dict[str, Any]] | None = None) -> None:
        """Sets the chat's own model ("" follows Settings) and whether openrouter/auto may pick only hearing
        models; optionally its provider, its transcript mode and the tools fitting the new provider. A chat
        without a transcript decides again on its next question whether it needs one; so does one whose
        transcript was skipped, if its provider or transcript mode changed. A finished transcript is kept, also
        while the mode leaves it out, so it is never paid for twice."""
        session = self.get_session(session_id, owner_id)
        provider = provider or session.provider
        transcribe = transcribe or session.transcribe
        state, transcript = session.transcript_state, session.transcript
        retry = provider != session.provider or transcribe != session.transcribe
        if state == TRANSCRIPT_NONE or (retry and state == TRANSCRIPT_FAILED):
            state, transcript = TRANSCRIPT_PENDING, ""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET model = ?, audio_only = ?, transcript_state = ?, transcript = ?, "
                         "size_confirmed = '0', provider = ?, transcribe = ?, tools_json = ? "
                         "WHERE id = ? AND owner_id = ?",
                         (model, "1" if audio_only else "0", state, transcript, provider,
                          transcribe,
                          json.dumps(session.tools if tools is None else tools), session_id, owner_id))

    def confirm_size(self, session_id: str, owner_id: str) -> None:
        """Remembers that a question with the stored video was answered, so its size is not asked about again."""
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET size_confirmed = '1' WHERE id = ? AND owner_id = ?", (session_id, owner_id))

    def replace_video(self, session_id: str, owner_id: str, video: Path) -> None:
        """Stores another copy of the chat's video to send, e.g. one shrunk for a provider's size limit."""
        session = self.get_session(session_id, owner_id)
        mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/mov"}.get(Path(video).suffix.lower(), "video/mp4")
        data_url = f"data:{mime};base64," + base64.b64encode(Path(video).read_bytes()).decode("ascii")
        temporary = session.data_url_path.with_suffix(".tmp")
        temporary.write_text(data_url, encoding="ascii")
        os.chmod(temporary, 0o600)
        temporary.replace(session.data_url_path)
        for sound in [*session.directory.glob(f"{SOUND_FILE_PREFIX}*"), session.directory / NO_SOUND_FILE]:
            sound.unlink(missing_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET size_confirmed = '0' WHERE id = ? AND owner_id = ?", (session_id, owner_id))

    def update_transcript(self, session_id: str, owner_id: str, state: str, transcript: str = "") -> None:
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET transcript = ?, transcript_state = ? WHERE id = ? AND owner_id = ?",
                         (transcript, state, session_id, owner_id))

    def append_transcript(self, session_id: str, owner_id: str, transcript: str) -> None:
        """Adds a transcript made after the chat started at its end, so what is cached before it stays valid.
        No speech is noted like a transcript with no speech."""
        if not transcript:
            self.update_transcript(session_id, owner_id, TRANSCRIPT_DONE, "")
            return
        self.record_turn(session_id, owner_id, [{"role": "user", "content": TRANSCRIPT_LATER_INTRO + transcript,
                                                 TRANSCRIPT_ENTRY: True}])
        self.update_transcript(session_id, owner_id, TRANSCRIPT_APPENDED)

    def update_source(self, session_id: str, owner_id: str, source_url: str, video_info: dict[str, Any] | None) -> None:
        """Records the online source of a chat that was started before links were stored."""
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET source_url = ?, video_info_json = ? WHERE id = ? AND owner_id = ?",
                         (source_url, json.dumps(video_info) if video_info else "", session_id, owner_id))

    def reassign_owner(self, old_owner_id: str, new_owner_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET owner_id = ? WHERE owner_id = ?", (new_owner_id, old_owner_id))

    def delete_session(self, session_id: str, owner_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            # Serialize deletion and owner changes before checking authorization. Keep the
            # row registered until all file operations succeed; partial removal is retryable.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM sessions WHERE id = ? AND owner_id = ?",
                               (session_id, owner_id)).fetchone()
            if not row:
                raise SessionNotFound(session_id)
            conn.execute("DELETE FROM sessions WHERE id = ? AND owner_id = ?", (session_id, owner_id))
            directory = self.sessions_dir / session_id
            # Windows junctions need no special rights and are not reported by is_symlink().
            if directory.is_symlink() or directory.is_junction():
                raise OSError(f"Refusing to delete a linked session directory: {directory}")
            try:
                children = list(directory.iterdir())
            except FileNotFoundError:
                children = []
            # Session files are flat. Never recurse into unexpected directories or
            # follow a directory link into unrelated data.
            for child in children:
                if child.is_dir() and not child.is_symlink():
                    raise IsADirectoryError(child)
            for child in children:
                child.unlink(missing_ok=True)
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass

    def build_follow_up_messages(self, session_id: str, owner_id: str, question: str,
                                 hears_sound: bool = False) -> list[dict[str, Any]]:
        session = replace(self.get_session(session_id, owner_id), hears_sound=hears_sound)
        return _merge_user_runs([*session.messages, {"role": "user", "content": question}])

    def set_rounds_in_context(self, session_id: str, owner_id: str, indices: list[int], in_context: bool) -> None:
        """Takes the rounds of the history entries at indices out of what is sent to the model, or puts them
        back; the Chat history keeps showing them. The rounds are found again in the stored history, inside
        the transaction."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            history = self._locked_history(conn, session_id, owner_id)
            for position in _round_positions(history, indices):
                if in_context:
                    history[position].pop(EXCLUDED, None)
                else:
                    history[position][EXCLUDED] = True
            conn.execute("UPDATE sessions SET history_json = ? WHERE id = ? AND owner_id = ?",
                         (json.dumps(history), session_id, owner_id))

    def delete_rounds(self, session_id: str, owner_id: str, indices: list[int]) -> None:
        """Deletes the rounds of the history entries at indices, with the images the model asked for in them."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            history = self._locked_history(conn, session_id, owner_id)
            deleted = _round_positions(history, indices)
            images = [Path(part["image_url"]["url"][len(SESSION_FILE_PREFIX):]).name
                      for position in deleted if isinstance(history[position].get("content"), list)
                      for part in history[position]["content"] if part.get("type") == "image_url"
                      and str((part.get("image_url") or {}).get("url", "")).startswith(SESSION_FILE_PREFIX)]
            kept = [entry for position, entry in enumerate(history) if position not in deleted]
            conn.execute("UPDATE sessions SET history_json = ? WHERE id = ? AND owner_id = ?",
                         (json.dumps(kept), session_id, owner_id))
        # Only once the history no longer refers to them.
        for name in images:
            (self.sessions_dir / session_id / name).unlink(missing_ok=True)

    @staticmethod
    def _locked_history(conn: sqlite3.Connection, session_id: str, owner_id: str) -> list[dict[str, Any]]:
        row = conn.execute("SELECT history_json FROM sessions WHERE id = ? AND owner_id = ?",
                           (session_id, owner_id)).fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        return json.loads(row[0])
