"""SceneAccess on the command line: list the stored chats, show one, and compare models on one.

python -m sceneaccess.cli chats
python -m sceneaccess.cli show "<chat title or ID>"
python -m sceneaccess.cli compare "<chat title or ID>" <model> [<model> ...]

compare answers the chat's first question as SceneAccess would today: with the current system prompt, without
the chat's history, and with its transcript as the chat's transcript mode asks. It works on a copy, so the chat
stays unchanged; a missing transcript is not made. A model "llama.cpp" (or "llama.cpp:<name>") goes to the
llama.cpp server from the settings, any other one to OpenRouter."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import TextIO

from .app_window import LEGACY_OWNER_ID, OWNER_ID, open_store, resolve_openrouter_key
from .chat_history import chat_history_rows
from .config import AppConfig, load_config
from .models import ModelCatalog, routing
from .openrouter import OpenRouterClient
from .prompts import current_system_prompt
from .providers import LLAMA_CPP, LLAMA_CPP_DEFAULT_MODEL, OPENROUTER, OPENROUTER_URL, PROVIDER_LABELS
from .session_store import VideoSession, VideoSessionStore
from .tools import ToolRunner, provider_tools
from .transcript import model_hears


def default_data_dir() -> Path:
    """Where the app keeps config.json and the chats: wx's user data folder, without starting wx on Windows."""
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "SceneAccess"
    import wx
    app = wx.App(False)
    app.SetAppName("SceneAccess")
    return Path(wx.StandardPaths.Get().GetUserDataDir())


def find_chat(store: VideoSessionStore, chat: str) -> VideoSession:
    sessions = store.list_sessions(OWNER_ID)
    found = [session for session in sessions if chat in (session.id, session.title)]
    if len(found) != 1:
        hint = "several chats have this title; give its ID" if found else "no chat has this title or ID"
        raise SystemExit(f"{chat!r}: {hint}. See: python -m sceneaccess.cli chats")
    return found[0]


def list_chats(store: VideoSessionStore, out: TextIO) -> None:
    for session in store.list_sessions(OWNER_ID):
        outdated = " | outdated system prompt" if current_system_prompt(session.system_prompt) != session.system_prompt else ""
        print(f"{session.title} | {session.id} | {PROVIDER_LABELS.get(session.provider, session.provider)} | "
              f"model {session.model or '(Settings)'} | transcript {session.transcribe}, {session.transcript_state}"
              f"{outdated}", file=out)


def show_chat(session: VideoSession, out: TextIO) -> None:
    print(f"{session.title} | {session.id} | {PROVIDER_LABELS.get(session.provider, session.provider)} | "
          f"model {session.model or '(Settings)'}", file=out)
    print(f"\n== System prompt\n{session.system_prompt or '(none)'}", file=out)
    for _shown, full, _index, _kind in chat_history_rows(session.initial_question, session.history,
                                                         session.transcript_state, session.transcript):
        print(f"\n{full}", file=out)


def compare(data_dir: Path, session: VideoSession, models: list[str], out: TextIO) -> None:
    config = load_config(data_dir / "config.json")
    catalog = ModelCatalog(OPENROUTER_URL)
    work = Path(tempfile.mkdtemp(prefix="sceneaccess-compare-"))
    try:
        # The copy holds only this chat, so tool images and anything else written go there.
        (work / "sessions").mkdir()
        shutil.copytree(session.directory, work / "sessions" / session.id)
        copy = replace(session, data_url_path=work / "sessions" / session.id / session.data_url_path.name,
                       history=[], system_prompt=current_system_prompt(session.system_prompt))
        print(f"{session.title} | transcript mode {session.transcribe}, {session.transcript_state} | "
              f"system prompt updated: {copy.system_prompt != session.system_prompt}", file=out)
        for name in models:
            compare_one(config, catalog, copy, name, out)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def compare_one(config: AppConfig, catalog: ModelCatalog, session: VideoSession, name: str, out: TextIO) -> None:
    provider = LLAMA_CPP if name.split(":")[0] == LLAMA_CPP else OPENROUTER
    model = ((name.partition(":")[2] or config.llama_cpp_model or LLAMA_CPP_DEFAULT_MODEL)
             if provider == LLAMA_CPP else name)
    session = replace(session, provider=provider, model=model)
    hears = model_hears(session, catalog)
    session = replace(session, hears_sound=hears)
    messages = session.messages
    # Compared as JSON text, so line breaks in the transcript match their escaped form in the request.
    sent = bool(session.transcript) and (json.dumps(session.transcript, ensure_ascii=False)[1:-1]
                                         in json.dumps(messages, ensure_ascii=False))
    if provider == LLAMA_CPP:
        # Only the llama.cpp key goes to the user's server, never the OpenRouter key.
        client = OpenRouterClient(config.llama_cpp_api_key, base_url=config.llama_cpp_url)
        options = {}
    else:
        client = OpenRouterClient(resolve_openrouter_key(config) or "", ignored_providers=config.ignored_providers)
        options = routing(model, session.audio_only, catalog, config.ignored_providers, config.excluded_models)
    runner = ToolRunner(session, config.youtube_cookies)
    steps: list[str] = []
    print(f"\n===== {name} | hears: {hears} | transcript sent: {sent} =====", file=out)
    started = time.monotonic()
    try:
        turn = client.run_turn(replace(session, tools=provider_tools(session.tools, provider)), messages,
                               runner.run, lambda kind, text: steps.append(f"{kind}: {text}"), options,
                               max_tool_rounds=config.max_tool_rounds)
        completion = turn.completion
        print(f"Time: {time.monotonic() - started:.0f} s | answered by {completion.model} | requests "
              f"{completion.requests} | prompt tokens {completion.prompt_tokens}", file=out)
        for step in steps:
            print(f"Step: {step}", file=out)
        print(completion.answer, file=out)
    except Exception as error:
        print(f"Failed after {time.monotonic() - started:.0f} s: {error}", file=out)
    finally:
        runner.cleanup_files()


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m sceneaccess.cli", description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, help="folder with config.json and chats (default: the app's)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("chats", help="list the stored chats")
    show = commands.add_parser("show", help="show a chat's system prompt and history")
    show.add_argument("chat", help="title or ID")
    compare_parser = commands.add_parser("compare", help="answer a chat's first question with several models")
    compare_parser.add_argument("chat", help="title or ID")
    compare_parser.add_argument("models", nargs="+", help='OpenRouter model IDs, or "llama.cpp[:name]"')
    args = parser.parse_args(argv)
    data_dir = args.data_dir or default_data_dir()
    store = open_store(data_dir / "config.json", OWNER_ID, LEGACY_OWNER_ID)
    if args.command == "chats":
        list_chats(store, out)
    elif args.command == "show":
        show_chat(find_chat(store, args.chat), out)
    else:
        compare(data_dir, find_chat(store, args.chat), args.models, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
