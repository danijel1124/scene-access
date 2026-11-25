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

from .config import resolve_openrouter_key
from .session_store import LEGACY_OWNER_ID, OWNER_ID, open_store
from .chat_history import chat_history_rows
from .config import AppConfig, load_config
from . import answering
from .models import ModelCatalog
from .prompts import current_system_prompt
from .providers import LLAMA_CPP, LLAMA_CPP_DEFAULT_MODEL, OPENROUTER, OPENROUTER_URL, PROVIDER_LABELS
from .session_store import REMOVED, TRANSCRIBE_MODES, VideoSession, VideoSessionStore
from .hearing import wants_transcript


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
                                                         session.transcript_state, session.transcript,
                                                         session.sections):
        print(f"\n{full}", file=out)


SAME_REVIEWER = "same"


def compare(data_dir: Path, session: VideoSession, models: list[str], out: TextIO, transcribe: str = "",
            section_seconds: int | None = None, reviewer: str | None = None, runs: int = 1,
            follow_up: str = "") -> None:
    """transcribe sets the copy's transcript mode; empty keeps the chat's. section_seconds describes a longer
    video in sections of at most that length, as the app does; None keeps the chat's, 0 sends it whole.
    reviewer is the OpenRouter model that checks the sections; None keeps the one in Settings, "same" the
    model that wrote them. runs repeats each model's answer, each time as a new chat; follow_up is asked after
    each answer, with it as the chat so far, as the app sends a later question."""
    config = load_config(data_dir / "config.json")
    if reviewer is not None:
        # "same" rather than an empty argument, which Windows PowerShell does not pass on.
        config = replace(config, section_reviewer_model="" if reviewer == SAME_REVIEWER else reviewer)
    catalog = ModelCatalog(OPENROUTER_URL)
    work = Path(tempfile.mkdtemp(prefix="sceneaccess-compare-"))
    try:
        # The copy holds only this chat, so tool images and anything else written go there.
        (work / "sessions").mkdir()
        shutil.copytree(session.directory, work / "sessions" / session.id)
        copy = replace(session, data_url_path=work / "sessions" / session.id / session.data_url_path.name,
                       history=[], system_prompt=current_system_prompt(session.system_prompt),
                       transcribe=transcribe or session.transcribe, sections=None,
                       section_seconds=session.section_seconds if section_seconds is None else section_seconds)
        print(f"{session.title} | transcript mode {copy.transcribe}, {session.transcript_state} | "
              f"system prompt updated: {copy.system_prompt != session.system_prompt} | "
              f"sections: {copy.section_seconds or 'none'}"
              + (f", checked by {config.section_reviewer_model or 'the same model'}" if copy.section_seconds else ""),
              file=out)
        for name in models:
            for run in range(runs):
                if runs > 1:
                    print(f"\n########## Run {run + 1} of {runs}, a new chat", file=out, flush=True)
                compare_one(config, catalog, copy, name, out, follow_up)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def compare_one(config: AppConfig, catalog: ModelCatalog, session: VideoSession, name: str, out: TextIO,
                follow_up: str = "") -> None:
    """Answers the chat's first question with one model, through the app's own pipeline, and writes it all as it
    happens, so a run can be followed while it goes on, and what came before a failure stays."""
    provider = LLAMA_CPP if name.split(":")[0] == LLAMA_CPP else OPENROUTER
    model = ((name.partition(":")[2] or config.llama_cpp_model or LLAMA_CPP_DEFAULT_MODEL)
             if provider == LLAMA_CPP else name)
    session = replace(session, provider=provider, model=model)
    openrouter_key = resolve_openrouter_key(config)
    started = time.monotonic()
    shown = {"kind": "", "text": False}

    def piece(kind: str, text: str) -> None:
        shown["text"] = shown["text"] or kind == "text"
        if kind != shown["kind"]:
            label = {"thinking": "Thinking", "text": "Writing"}.get(kind, kind)
            print(f"\n[{label}, {time.monotonic() - started:.0f} s]", file=out)
            shown["kind"] = kind
        print(text, end="", file=out, flush=True)

    def step(kind: str, text: str) -> None:
        shown["kind"] = ""
        print(f"\n[Step, {time.monotonic() - started:.0f} s] {kind}: {text}", file=out, flush=True)

    runner = None
    try:
        # As the app decides, but no transcript is made: compare works on a copy of the chat.
        request = answering.prepare(config, catalog, session, session.initial_question, initial=True,
                                    openrouter_key=openrouter_key)
        if request.sectioned:
            sent = request.session.sends_transcript
        else:
            # Compared as JSON text, so line breaks in the transcript match their escaped form in the request.
            sent = bool(request.session.transcript) and (
                json.dumps(request.session.transcript, ensure_ascii=False)[1:-1]
                in json.dumps(request.messages, ensure_ascii=False))
        if wants_transcript(request.session, request.hears) and not sent:
            print("The transcript mode asks for a transcript the chat does not have; compare makes none.", file=out)
        # A model on a llama.cpp server that hears gets the sound as a track of its own, as in the app.
        track = " (sound sent as a track)" if request.hears and request.llama_cpp else ""
        print(f"\n===== {name} | hears: {request.hears}{track} | transcript sent: {sent} =====", file=out, flush=True)
        started = time.monotonic()
        turn, runner = answering.answer(request, on_event=step, on_delta=piece)
        completion = turn.completion
        if not shown["text"]:
            # Not streamed, e.g. by a server that does not stream: the answer comes in one piece.
            print(f"\n{completion.answer}", file=out)
        if turn.entries and turn.entries[-1].get(REMOVED):
            print(f"\n\nRemoved from the answer:\n{turn.entries[-1][REMOVED]}", file=out, flush=True)
        print(f"\n\nTime: {time.monotonic() - started:.0f} s | answered by {completion.model} | requests "
              f"{completion.requests} | prompt tokens {completion.prompt_tokens}", file=out, flush=True)
        if follow_up and request.sectioned:
            print("\nThe follow-up is skipped: a chat described in sections would need its stored sections.",
                  file=out, flush=True)
        elif follow_up:
            # As the app sends a later question: the chat with this answer so far, then the question.
            print(f"\n----- Follow-up: {follow_up}", file=out, flush=True)
            later = answering.prepare(config, catalog, replace(request.session, history=turn.entries), follow_up,
                                      initial=False, openrouter_key=openrouter_key)
            started, shown["kind"], shown["text"] = time.monotonic(), "", False
            reply, follow_runner = answering.answer(later, on_event=step, on_delta=piece)
            if follow_runner is not None:
                follow_runner.cleanup_files()
            if not shown["text"]:
                print(f"\n{reply.completion.answer}", file=out)
            print(f"\n\nFollow-up time: {time.monotonic() - started:.0f} s | requests {reply.completion.requests}",
                  file=out, flush=True)
    except Exception as error:
        print(f"\n\nFailed after {time.monotonic() - started:.0f} s: {error}", file=out, flush=True)
    finally:
        if runner is not None:
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
    compare_parser.add_argument("--transcribe", choices=TRANSCRIBE_MODES,
                                help="transcript mode for this comparison only (default: the chat's)")
    compare_parser.add_argument("--sections", type=int, metavar="SECONDS",
                                help="describe a longer video in sections of at most this many seconds (0: whole)")
    compare_parser.add_argument("--reviewer", metavar="MODEL",
                                help=f"OpenRouter model that checks the sections (default: Settings; {SAME_REVIEWER} "
                                     "for the model that wrote them)")
    compare_parser.add_argument("--runs", type=int, default=1, metavar="N",
                                help="answer N times with each model, each time as a new chat")
    compare_parser.add_argument("--follow-up", default="", metavar="QUESTION",
                                help="ask this after each answer, with the answer as the chat so far")
    args = parser.parse_args(argv)
    data_dir = args.data_dir or default_data_dir()
    store = open_store(data_dir / "config.json", OWNER_ID, LEGACY_OWNER_ID)
    if args.command == "chats":
        list_chats(store, out)
    elif args.command == "show":
        show_chat(find_chat(store, args.chat), out)
    else:
        compare(data_dir, find_chat(store, args.chat), args.models, out, args.transcribe or "", args.sections,
                args.reviewer, max(1, args.runs), args.follow_up)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
