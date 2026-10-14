from __future__ import annotations

import base64
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SessionNotFound(KeyError):
    """The requested private session does not exist for this owner."""


# Fields of a stored message that belong to the chat protocol. Everything else (usage, sources) is local.
WIRE_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name", "reasoning_details")
# Images the model asked for are stored as files in the session folder and referenced like this.
SESSION_FILE_PREFIX = "session-file:"

COLUMNS = ("id", "owner_id", "title", "source_path", "model", "initial_question", "history_json",
           "system_prompt", "tools_json", "source_url", "video_info_json", "transcript", "transcript_state", "audio_only")
# Columns added after the first release, with the value older chats get. Older chats get no transcript,
# so opening them does not change what is sent and their cache stays valid.
ADDED_COLUMNS = (("system_prompt", "''"), ("tools_json", "'[]'"), ("source_url", "''"), ("video_info_json", "''"),
                 ("transcript", "''"), ("transcript_state", "'none'"), ("audio_only", "'0'"))

# transcript_state: "pending" until the first request decides whether the model needs a transcript,
# "none" if it hears the video itself, "done" (text may be empty: no speech), or "failed".
TRANSCRIPT_PENDING, TRANSCRIPT_NONE, TRANSCRIPT_DONE, TRANSCRIPT_FAILED = "pending", "none", "done", "failed"
TRANSCRIPT_INTRO = ("You cannot hear this video, so here is an automatic transcript of its speech. Speech "
                    "recognition may contain errors; times are mm:ss from the start of the video.\n\n")


@dataclass
class VideoSession:
    id: str
    owner_id: str
    title: str
    source_path: str
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
    def messages(self) -> list[dict[str, Any]]:
        # OpenRouter reads explicit cache breakpoints from content parts, not from the message.
        system = [{"role": "system", "content": self.system_prompt}] if self.system_prompt else []
        # The transcript comes before the video's cache breakpoint, so it is cached together with the video.
        transcript = [{"type": "text", "text": TRANSCRIPT_INTRO + self.transcript}] if self.transcript else []
        return [*system, {
            "role": "user",
            "content": [
                {"type": "text", "text": self.initial_question},
                *transcript,
                {"type": "video_url", "video_url": {"url": self.video_data_url}, "cache_control": {"type": "ephemeral"}},
            ],
        }, *(self.wire_message(message) for message in self.history)]

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
                       audio_only: bool = False) -> VideoSession:
        source_path = Path(source_path).resolve()
        mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/mov"}.get(source_path.suffix.lower())
        if not source_path.is_file() or not mime:
            raise FileNotFoundError(source_path)
        # Read the video before creating anything, so a failed read leaves no empty session behind.
        data_url = f"data:{mime};base64," + base64.b64encode(source_path.read_bytes()).decode("ascii")
        session_id = "vid_" + uuid.uuid4().hex
        directory = self.sessions_dir / session_id
        directory.mkdir(mode=0o700)
        data_url_path = directory / "context.data-url"
        data_url_path.write_text(data_url, encoding="ascii")
        os.chmod(data_url_path, 0o600)
        values = (session_id, owner_id, title, str(source_path), model, initial_question, "[]", system_prompt,
                  json.dumps(tools or []), source_url, json.dumps(video_info) if video_info else "",
                  "", transcript_state, "1" if audio_only else "0")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"INSERT INTO sessions ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", values)
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
            audio_only=values["audio_only"] == "1",
        )

    def list_sessions(self, owner_id: str) -> list[VideoSession]:
        with sqlite3.connect(self.db_path) as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM sessions WHERE owner_id = ? ORDER BY rowid DESC", (owner_id,)
            )]
        return [self.get_session(session_id, owner_id) for session_id in ids]

    def record_turn(self, session_id: str, owner_id: str, entries: list[dict[str, Any]]) -> None:
        """Appends one turn: the question (if any), tool calls, tool results, images and the answer."""
        session = self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET history_json = ? WHERE id = ? AND owner_id = ?",
                         (json.dumps([*session.history, *entries]), session_id, owner_id))

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
        """Replaces a chat's prompt and tools. The next request decides about a transcript again and
        caches the video again."""
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET system_prompt = ?, tools_json = ?, audio_only = ?, transcript = '', "
                         "transcript_state = ? WHERE id = ? AND owner_id = ?",
                         (system_prompt, json.dumps(tools), "1" if audio_only else "0", TRANSCRIPT_PENDING,
                          session_id, owner_id))

    def update_transcript(self, session_id: str, owner_id: str, state: str, transcript: str = "") -> None:
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET transcript = ?, transcript_state = ? WHERE id = ? AND owner_id = ?",
                         (transcript, state, session_id, owner_id))

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
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM sessions WHERE id = ? AND owner_id = ?", (session_id, owner_id))
        directory = self.sessions_dir / session_id
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()

    def build_follow_up_messages(self, session_id: str, owner_id: str, question: str) -> list[dict[str, Any]]:
        session = self.get_session(session_id, owner_id)
        return [*session.messages, {"role": "user", "content": question}]
