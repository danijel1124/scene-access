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
from .models import ModelCatalog, llama_cpp_catalog, routing
from .openrouter import LLAMA_CPP_READ_TIMEOUT, OpenRouterClient
from .prompts import current_system_prompt
from .providers import LLAMA_CPP, LLAMA_CPP_DEFAULT_MODEL, OPENROUTER, OPENROUTER_URL, PROVIDER_LABELS
from .session_store import REMOVED, TRANSCRIBE_MODES, VideoSession, VideoSessionStore
from .tools import ToolRunner, provider_tools
from .sections import (Reviewer, cleaned_whole_answer, cut_sections, describe_in_sections, needs_sections,
                       section_sound, video_duration)
from .transcript import model_hears, sound_parts, wants_transcript, with_sound


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
    provider = LLAMA_CPP if name.split(":")[0] == LLAMA_CPP else OPENROUTER
    model = ((name.partition(":")[2] or config.llama_cpp_model or LLAMA_CPP_DEFAULT_MODEL)
             if provider == LLAMA_CPP else name)
    session = replace(session, provider=provider, model=model)
    duration = video_duration(session) if session.section_seconds else 0.0
    sectioned = needs_sections(session, duration)
    # As the app sends: a model on a llama.cpp server that hears gets the sound as a track of its own.
    hears = model_hears(session, llama_cpp_catalog(config.llama_cpp_url) if provider == LLAMA_CPP else catalog)
    sound = []
    if hears and provider == LLAMA_CPP and not sectioned:
        try:
            sound = sound_parts(session)
        except Exception as error:
            print(f"The sound could not be prepared, so the model does not hear it: {error}", file=out)
        hears = bool(sound)
    session = replace(session, hears_sound=hears)
    if sectioned:
        messages, sent = [], session.sends_transcript
    else:
        messages = with_sound(session.messages, sound)
        # Compared as JSON text, so line breaks in the transcript match their escaped form in the request.
        sent = bool(session.transcript) and (json.dumps(session.transcript, ensure_ascii=False)[1:-1]
                                             in json.dumps(messages, ensure_ascii=False))
    if wants_transcript(session, hears) and not sent:
        print("The transcript mode asks for a transcript the chat does not have; compare makes none.", file=out)
    if provider == LLAMA_CPP:
        # Only the llama.cpp key goes to the user's server, never the OpenRouter key.
        client = OpenRouterClient(config.llama_cpp_api_key, base_url=config.llama_cpp_url,
                                  read_timeout=LLAMA_CPP_READ_TIMEOUT)
        options = {}
    else:
        client = OpenRouterClient(resolve_openrouter_key(config) or "", ignored_providers=config.ignored_providers)
        options = routing(model, session.audio_only, catalog, config.ignored_providers, config.excluded_models)
    runner = ToolRunner(session, config.youtube_cookies)
    track = sectioned and hears and provider == LLAMA_CPP
    print(f"\n===== {name} | hears: {hears}{' (sound sent as a track)' if sound or track else ''} | "
          f"transcript sent: {sent} =====", file=out, flush=True)
    started = time.monotonic()
    # Everything is written as it happens, so a run can be followed in the output while it goes on, and what
    # came before a failure stays.
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

    tooled = replace(session, tools=provider_tools(session.tools, provider))
    try:
        reviewer = None
        if sectioned and config.section_reviewer_model:
            key = resolve_openrouter_key(config)
            if not key:
                raise ValueError(f"The reviewer model {config.section_reviewer_model} runs on OpenRouter, and no "
                                 "OpenRouter API key is configured.")
            reviewer = Reviewer(OpenRouterClient(key, ignored_providers=config.ignored_providers),
                                config.section_reviewer_model,
                                routing(config.section_reviewer_model, False, catalog, config.ignored_providers,
                                        config.excluded_models))
        if sectioned:
            # As in the app, but the progress stays with this comparison.
            turn = describe_in_sections(
                client, tooled, session.initial_question, cut_sections(session, duration),
                lambda: ToolRunner(session, config.youtube_cookies), lambda _progress: None, duration=duration,
                sound=(lambda section: section_sound(session, section)) if track else None,
                on_event=step, on_delta=piece, routing=options, max_tool_rounds=config.max_tool_rounds,
                reviewer=reviewer)
        else:
            turn = client.run_turn(tooled, messages, runner.run, step, options, max_tool_rounds=config.max_tool_rounds,
                                   on_delta=piece)
            if not turn.completion.cut_off:
                # As the app stores it, so the follow-up gets the answer the chat would keep.
                turn = cleaned_whole_answer(turn, session)
                if turn.entries and turn.entries[-1].get(REMOVED):
                    print(f"\n\nRemoved from the answer:\n{turn.entries[-1][REMOVED]}", file=out, flush=True)
        completion = turn.completion
        if not shown["text"]:
            # Not streamed, e.g. by a server that does not stream: the answer comes in one piece.
            print(f"\n{completion.answer}", file=out)
        cut = " | CUT OFF at the token limit, not a whole answer" if completion.cut_off else ""
        print(f"\n\nTime: {time.monotonic() - started:.0f} s | answered by {completion.model} | requests "
              f"{completion.requests} | prompt tokens {completion.prompt_tokens}{cut}", file=out, flush=True)
        if follow_up and sectioned:
            print("\nThe follow-up is skipped: a chat described in sections would need its stored sections.",
                  file=out, flush=True)
        elif follow_up:
            # As the app sends a later question: the first request, the answer with its tool rounds, the question.
            print(f"\n----- Follow-up: {follow_up}", file=out, flush=True)
            later = [*messages, *(session.wire_message(entry) for entry in turn.entries),
                     {"role": "user", "content": follow_up}]
            started, shown["kind"], shown["text"] = time.monotonic(), "", False
            answer = client.run_turn(tooled, later, runner.run, step, options, max_tool_rounds=config.max_tool_rounds,
                                     on_delta=piece).completion
            if not shown["text"]:
                print(f"\n{answer.answer}", file=out)
            print(f"\n\nFollow-up time: {time.monotonic() - started:.0f} s | requests {answer.requests}",
                  file=out, flush=True)
    except Exception as error:
        print(f"\n\nFailed after {time.monotonic() - started:.0f} s: {error}", file=out, flush=True)
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
