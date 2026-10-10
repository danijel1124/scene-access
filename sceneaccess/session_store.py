from __future__ import annotations

import base64
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SessionNotFound(KeyError):
    """The requested private session does not exist for this owner."""


@dataclass
class VideoSession:
    id: str
    owner_id: str
    title: str
    source_path: str
    model: str
    initial_question: str
    history: list[dict[str, str]]
    data_url_path: Path

    @property
    def source_missing(self) -> bool:
        return not Path(self.source_path).is_file()

    @property
    def video_data_url(self) -> str:
        return self.data_url_path.read_text(encoding="ascii")

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [{
            "role": "user",
            "cache_control": {"type": "ephemeral"},
            "content": [
                {"type": "text", "text": self.initial_question},
                {"type": "video_url", "video_url": {"url": self.video_data_url}},
            ],
        }, *self.history]


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
        os.chmod(self.db_path, 0o600)

    def data_url_path(self, session_id: str) -> Path:
        return self.sessions_dir / session_id / "context.data-url"

    def create_session(self, *, owner_id: str, source_path: Path, title: str, model: str, initial_question: str) -> VideoSession:
        source_path = Path(source_path).resolve()
        mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/mov"}.get(source_path.suffix.lower())
        if not source_path.is_file() or not mime:
            raise FileNotFoundError(source_path)
        session_id = "vid_" + uuid.uuid4().hex
        directory = self.sessions_dir / session_id
        directory.mkdir(mode=0o700)
        data_url_path = directory / "context.data-url"
        data_url_path.write_text(
            f"data:{mime};base64," + base64.b64encode(source_path.read_bytes()).decode("ascii"), encoding="ascii"
        )
        os.chmod(data_url_path, 0o600)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (session_id, owner_id, title, str(source_path), model, initial_question, "[]"))
        return self.get_session(session_id, owner_id)

    def get_session(self, session_id: str, owner_id: str) -> VideoSession:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ? AND owner_id = ?", (session_id, owner_id)).fetchone()
        if not row:
            raise SessionNotFound(session_id)
        return VideoSession(*row[:6], json.loads(row[6]), data_url_path=self.sessions_dir / session_id / "context.data-url")

    def list_sessions(self, owner_id: str) -> list[VideoSession]:
        with sqlite3.connect(self.db_path) as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM sessions WHERE owner_id = ? ORDER BY rowid DESC", (owner_id,)
            )]
        return [self.get_session(session_id, owner_id) for session_id in ids]

    def record_answer(self, session_id: str, owner_id: str, answer: str) -> None:
        session = self.get_session(session_id, owner_id)
        history = [*session.history, {"role": "assistant", "content": answer}]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET history_json = ? WHERE id = ? AND owner_id = ?",
                         (json.dumps(history), session_id, owner_id))

    def record_follow_up(self, session_id: str, owner_id: str, question: str, answer: str) -> None:
        session = self.get_session(session_id, owner_id)
        history = [*session.history, {"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET history_json = ? WHERE id = ? AND owner_id = ?",
                         (json.dumps(history), session_id, owner_id))

    def update_source_path(self, session_id: str, owner_id: str, source_path: Path) -> None:
        source_path = Path(source_path).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        self.get_session(session_id, owner_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET source_path = ? WHERE id = ? AND owner_id = ?",
                         (str(source_path), session_id, owner_id))

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
