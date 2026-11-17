"""Prints the newest chat's title, model, questions and answers, read-only."""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
db = Path(os.environ["APPDATA"]) / "SceneAccess" / "chats" / "sceneaccess.db"
with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
    row = conn.execute("SELECT id, title, model, provider, initial_question, history_json, system_prompt "
                       "FROM sessions ORDER BY rowid DESC LIMIT 1").fetchone()
session_id, title, model, provider, question, history_json, system_prompt = row
print(f"Chat: {title} | {session_id} | model {model or '(Settings)'} | {provider}")
print(f"Prompt-Länge des Chats: {len(system_prompt)} Zeichen")
print(f"\n== Erste Frage:\n{question}")
for entry in json.loads(history_json):
    content = entry.get("content")
    if entry["role"] == "user" and isinstance(content, str):
        print(f"\n== Frage:\n{content}")
    elif entry["role"] == "assistant" and not entry.get("tool_calls"):
        print(f"\n== Antwort ({(entry.get('usage') or {}).get('model', '?')}):\n{content}")
