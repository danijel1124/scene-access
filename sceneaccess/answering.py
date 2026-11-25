"""How a question about a chat's video is answered, the same in the app and on the command line: who answers
(OpenRouter, or the user's llama.cpp server with OpenRouter for the extras), whether the model hears the sound,
whether the chat needs a transcript, whether a long video is described in sections, and the answer checked and
cleaned. The app puts its window and the chat store around it, the command line its printing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Callable

from .config import AppConfig
from .models import ModelCatalog, llama_cpp_catalog, routing
from .openrouter import LLAMA_CPP_READ_TIMEOUT, DeltaHandler, EventHandler, OpenRouterClient, Turn, check_whole
from .providers import LLAMA_CPP
from .request_control import RequestCancelled, RequestControl
from .section_cuts import cut_sections, needs_sections, section_sound, video_duration
from .sections import Reviewer, cleaned_whole_answer, describe_in_sections
from .session_store import TRANSCRIPT_NONE, TRANSCRIPT_PENDING, VideoSession
from .tools import ToolResult, ToolRunner, provider_tools
from .transcript import model_hears, sound_parts, wants_transcript, with_sound

# Called when the chat's transcript is still to be decided, with the chat, whether the model answering now needs
# one, and OpenRouter (None without a key); returns the chat as it is afterwards.
TranscriptStep = Callable[[VideoSession, bool, "OpenRouterClient | None"], VideoSession]
NO_OPENROUTER_KEY = "No API key is configured. Enter one in Settings or set OPENROUTER_API_KEY."


def search_through_openrouter(client: OpenRouterClient, model: str, options: dict, query: str) -> ToolResult:
    """The web_search tool of a llama.cpp chat: OpenRouter's search, given only the query. options holds
    the provider and model exclusions from Settings."""
    text, sources = client.web_search(query, model, options)
    if sources:
        text += "\n\nSources:\n" + "\n".join(f"- {source['title']}: {source['url']}" for source in sources)
    count = len(sources)
    return ToolResult(text or "The search found nothing.",
                      caption=f"Searched the web for {query}: {count} source{'' if count == 1 else 's'}")


def clients(config: AppConfig, provider: str, openrouter_key: str | None,
            control: RequestControl | None = None) -> tuple[OpenRouterClient, OpenRouterClient | None]:
    """The client that answers the chat, and OpenRouter for the extras of a llama.cpp chat (None without a key;
    never given the chat's video). Only the llama.cpp key goes to the user's server, never the OpenRouter key."""
    openrouter = (OpenRouterClient(openrouter_key, control=control, ignored_providers=config.ignored_providers)
                  if openrouter_key else None)
    if provider == LLAMA_CPP:
        return OpenRouterClient(config.llama_cpp_api_key, base_url=config.llama_cpp_url, control=control,
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
    client: OpenRouterClient
    openrouter: OpenRouterClient | None
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
           save_sections: Callable[[dict[str, Any]], None] = lambda _progress: None) -> tuple[Turn, ToolRunner | None]:
    """Asks the model. Returns the answer and, for a video sent whole, the tool runner whose files belong to it:
    the caller commits them once the answer is stored, and cleans up in any case. An answer cut off at the token
    limit raises AnswerCutOff: stored, it would read as a whole one. save_sections stores each section's progress."""
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
        return turn, None
    runner = request.new_runner()
    try:
        turn = request.client.run_turn(request.tooled, request.messages, runner.run, on_event, request.options,
                                       max_tool_rounds=config.max_tool_rounds, on_delta=on_delta)
        check_whole(turn.completion)
        if request.initial:
            turn = cleaned_whole_answer(turn, session)
    except BaseException:
        runner.cleanup_files()
        raise
    return turn, runner
