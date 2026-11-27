"""How a question about a chat's video is answered, the same in the app and on the command line: who answers
(OpenRouter, or the user's llama.cpp server with OpenRouter for the extras), whether the model hears the sound,
whether the chat needs a transcript, whether a long video is described in sections, and the answer checked and
cleaned. The app puts its window and the chat store around it, the command line its printing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from ..llm.client import LLAMA_CPP_READ_TIMEOUT, ChatClient, DeltaHandler, EventHandler, Turn, added_usage, check_whole
from ..llm.models import ModelCatalog, llama_cpp_catalog, routing
from ..llm.tool_calls import ToolResult
from ..media.transcript import sound_parts
from ..providers import LLAMA_CPP
from ..request_control import RequestCancelled, RequestControl
from ..settings.config import AppConfig
from ..storage.session_store import MOMENTS, OTHER_LANGUAGE, REMOVED, TRANSCRIPT_NONE, TRANSCRIPT_PENDING, VideoSession
from .cleaning import clean_description
from .hearing import model_hears, wants_transcript, with_sound
from .language import other_language
from .moments import split_into_moments
from .section_cuts import cut_sections, needs_sections, section_sound, video_duration
from .section_review import Reviewer
from .sections import describe_in_sections
from .tools import ToolRunner, provider_tools

# Called when the chat's transcript is still to be decided, with the chat, whether the model answering now needs
# one, and OpenRouter (None without a key); returns the chat as it is afterwards.
TranscriptStep = Callable[[VideoSession, bool, "ChatClient | None"], VideoSession]
NO_OPENROUTER_KEY = "No API key is configured. Enter one in Settings or set OPENROUTER_API_KEY."


def search_through_openrouter(client: ChatClient, model: str, options: dict, query: str) -> ToolResult:
    """The web_search tool of a llama.cpp chat: OpenRouter's search, given only the query. options holds
    the provider and model exclusions from Settings."""
    text, sources = client.web_search(query, model, options)
    if sources:
        text += "\n\nSources:\n" + "\n".join(f"- {source['title']}: {source['url']}" for source in sources)
    count = len(sources)
    return ToolResult(text or "The search found nothing.",
                      caption=f"Searched the web for {query}: {count} source{'' if count == 1 else 's'}")


def clients(config: AppConfig, provider: str, openrouter_key: str | None,
            control: RequestControl | None = None) -> tuple[ChatClient, ChatClient | None]:
    """The client that answers the chat, and OpenRouter for the extras of a llama.cpp chat (None without a key;
    never given the chat's video). Only the llama.cpp key goes to the user's server, never the OpenRouter key."""
    openrouter = (ChatClient(openrouter_key, control=control, ignored_providers=config.ignored_providers)
                  if openrouter_key else None)
    if provider == LLAMA_CPP:
        return ChatClient(config.llama_cpp_api_key, base_url=config.llama_cpp_url, control=control,
                                read_timeout=LLAMA_CPP_READ_TIMEOUT), openrouter
    if openrouter is None:
        raise ValueError(NO_OPENROUTER_KEY)
    return openrouter, openrouter


@dataclass(frozen=True)
class Request:
    """A question decided: who answers it, with what, before anything is sent to the model."""
    config: AppConfig
    # The chat with the model that answers now and whether it hears the sound.
    session: VideoSession
    question: str
    initial: bool
    client: ChatClient
    openrouter: ChatClient | None
    options: dict[str, Any]
    search_options: dict[str, Any]
    # What is sent when the video goes whole; empty when it is described in sections.
    messages: list[dict[str, Any]]
    sectioned: bool
    duration: float
    hears: bool
    # The model the llama.cpp server runs, whatever the chat calls it, so stored sections of another model are
    # not continued.
    served_model: str
    reviewer: Reviewer | None
    control: RequestControl | None

    @property
    def llama_cpp(self) -> bool:
        return self.session.provider == LLAMA_CPP

    @property
    def tooled(self) -> VideoSession:
        """The chat with the tools its provider gets."""
        return replace(self.session, tools=provider_tools(self.session.tools, self.session.provider))

    def new_runner(self) -> ToolRunner:
        web_search = (partial(search_through_openrouter, self.openrouter, self.config.model, self.search_options)
                      if self.openrouter else None)
        return ToolRunner(self.session, self.config.youtube_cookies, control=self.control, web_search=web_search)


def prepare(config: AppConfig, catalog: ModelCatalog, session: VideoSession, question: str, *, initial: bool,
            openrouter_key: str | None, control: RequestControl | None = None,
            transcript_step: TranscriptStep | None = None) -> Request:
    """Decides how question is answered. session is the chat as stored, with its history for a later question.
    transcript_step makes or skips the transcript when the chat's mode leaves it to the model answering now; the
    command line, which never makes one, passes None."""
    client, openrouter = clients(config, session.provider, openrouter_key, control)
    # A chat on "default" asks the model that is in Settings now.
    session = replace(session, model=session.model or config.model_for(session.provider))
    llama_cpp = session.provider == LLAMA_CPP
    # Impossible routing is rejected before any potentially billable preprocessing. A llama.cpp server has no
    # providers or router to choose from.
    options = {} if llama_cpp else routing(session.model, session.audio_only, catalog, config.ignored_providers,
                                           config.excluded_models)
    # A video too long for the model is described in sections, also when a stopped description of the first
    # question is sent again; later questions then get the descriptions, not the video.
    duration = video_duration(session) if initial and session.section_seconds else 0.0
    sectioned = initial and needs_sections(session, duration)
    text_only = not initial and session.described_in_sections
    # Asked anew each time: the server may run another model by now.
    provider_catalog = llama_cpp_catalog(config.llama_cpp_url) if llama_cpp else catalog
    hears = model_hears(session, provider_catalog) and not text_only
    served = provider_catalog.model_ids() if llama_cpp and sectioned else []
    sound = []
    if hears and llama_cpp and not sectioned:
        # The server ignores a video's sound, so a model that hears gets it as a track of its own. Without one, it
        # does not hear this time, and the transcript mode decides as for any other.
        try:
            sound = sound_parts(session, control)
        except RequestCancelled:
            raise
        except Exception:
            sound = []
        hears = bool(sound)
    # Decided for the model answering now, which may be another one than last time. A failed or skipped transcript
    # is not tried again until Chat settings or the setup change.
    if transcript_step and session.transcript_state in (TRANSCRIPT_NONE, TRANSCRIPT_PENDING):
        session = transcript_step(session, wants_transcript(session, hears), openrouter)
        session = replace(session, model=session.model or config.model_for(session.provider))
    # Only a model that takes in the sound gets the prompt's blocks about it.
    session = replace(session, hears_sound=hears)
    messages = [] if sectioned else with_sound(
        session.messages if initial else session.follow_up_messages(question), sound)
    reviewer = None
    if sectioned and config.section_reviewer_model:
        if not openrouter:
            raise ValueError(f"The reviewer model {config.section_reviewer_model} in Settings runs on OpenRouter, "
                             "and no OpenRouter API key is configured.")
        reviewer = Reviewer(openrouter, config.section_reviewer_model,
                            routing(config.section_reviewer_model, False, catalog, config.ignored_providers,
                                    config.excluded_models))
    # Excluded providers never answer, the search included.
    search_options = (routing(config.model, False, catalog, config.ignored_providers, config.excluded_models)
                      if openrouter else {})
    return Request(config=config, session=session, question=question, initial=initial, client=client,
                   openrouter=openrouter, options=options, search_options=search_options, messages=messages,
                   sectioned=sectioned, duration=duration, hears=hears,
                   served_model=served[0] if len(served) == 1 else "", reviewer=reviewer, control=control)


def answer(request: Request, *, on_event: EventHandler | None = None, on_delta: DeltaHandler | None = None,
           save_sections: Callable[[dict[str, Any]], None] = lambda _progress: None,
           on_moments_delta: DeltaHandler | None = None) -> tuple[Turn, ToolRunner | None]:
    """Asks the model. Returns the answer and, for a video sent whole, the tool runner whose files belong to it:
    the caller commits them once the answer is stored, and cleans up in any case. An answer cut off at the token
    limit raises AnswerCutOff: stored, it would read as a whole one. save_sections stores each section's progress.
    A first answer is then split into its moments (with_moments); on_moments_delta gets that step's text as it
    arrives, for the command line, while the app shows only the step."""
    session, config, control = request.session, request.config, request.control
    if request.sectioned:
        # Each section's sound goes along for a model on a llama.cpp server that hears.
        sound = (lambda section: section_sound(session, section, control)) if request.hears and request.llama_cpp \
            else None
        turn = describe_in_sections(
            request.client, request.tooled, request.question, cut_sections(session, request.duration, control),
            request.new_runner, save_sections, duration=request.duration, sound=sound, on_event=on_event,
            on_delta=on_delta, routing=request.options, max_tool_rounds=config.max_tool_rounds,
            model_name=request.served_model, reviewer=request.reviewer)
        turn = with_language(request, turn)
        if control:
            control.check_cancelled()
        return with_moments(request, turn, on_event, on_moments_delta), None
    runner = request.new_runner()
    try:
        turn = request.client.run_turn(request.tooled, request.messages, runner.run, on_event, request.options,
                                       max_tool_rounds=config.max_tool_rounds, on_delta=on_delta)
        check_whole(turn.completion)
        turn = with_language(request, turn)
        # A cancel until the answer is whole discards it; one while it is split into moments costs only those.
        if control:
            control.check_cancelled()
        if request.initial:
            turn = with_moments(request, cleaned_whole_answer(turn, session), on_event, on_moments_delta)
    except BaseException:
        runner.cleanup_files()
        raise
    return turn, runner


def with_language(request: Request, turn: Turn) -> Turn:
    """The answer marked with the language it came in when that is clearly another than its question's
    (language.py). It is kept all the same: it is paid for, and it may be the more exact one."""
    found = other_language(request.question, turn.completion.answer)
    if not found or not turn.entries:
        return turn
    return Turn(entries=[*turn.entries[:-1], {**turn.entries[-1], OTHER_LANGUAGE: found}], completion=turn.completion)


def with_moments(request: Request, turn: Turn, on_event: EventHandler | None,
                 on_delta: DeltaHandler | None) -> Turn:
    """The first answer, as cleaned, with its moments (moments.py) on its entry and the step counted in its usage.
    Once for the whole answer, also after sections: their times are already the whole video's."""
    session, completion = request.session, turn.completion
    # The step is text only: the model that answered, not the router again, with only the providers excluded.
    model = completion.model if not request.llama_cpp and completion.model else session.model
    routing = {"provider": request.options["provider"]} if "provider" in request.options else {}
    try:
        duration = request.duration or video_duration(session)
        moments, usage = split_into_moments(request.client, replace(session, model=model), completion.answer,
                                            duration, routing, llama_cpp=request.llama_cpp, on_event=on_event,
                                            on_delta=on_delta)
    except Exception:
        # The answer is whole: a cancel or failure now costs only its moments (user's wish, 25.11.2026).
        return turn
    if usage is None or not turn.entries:
        return turn
    last = {**turn.entries[-1], "usage": added_usage(turn.entries[-1].get("usage"), usage),
            **({MOMENTS: moments} if moments else {})}
    completion = replace(completion, requests=completion.requests + int(usage.get("requests") or 1),
                         prompt_tokens=completion.prompt_tokens + int(usage.get("prompt_tokens") or 0))
    return Turn(entries=[*turn.entries[:-1], last], completion=completion)


def cleaned_whole_answer(turn: Turn, session: VideoSession) -> Turn:
    """The answer to the first question about the whole video, cleaned like a section's description, as Qwen3.5-9B
    also wrote what was said ("noting that sharp edges would otherwise dig in", 19.11.2026). What was taken out
    stays with the answer for the Chat history."""
    answer, notes = clean_description(turn.completion.answer,
                                      session.transcript if session.sends_transcript else "", whole=True)
    if not notes or not turn.entries:
        return turn
    entries = [*turn.entries[:-1], {**turn.entries[-1], "content": answer, REMOVED: "\n".join(notes)}]
    return Turn(entries=entries, completion=replace(turn.completion, answer=answer))
