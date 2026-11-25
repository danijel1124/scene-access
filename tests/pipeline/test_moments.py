import json
from dataclasses import replace
from pathlib import Path

import pytest

from sceneaccess.llm.client import Completion, Turn
from sceneaccess.pipeline import answering
from sceneaccess.pipeline.moments import FORMAT, parsed_moments, split_into_moments
from sceneaccess.request_control import RequestCancelled
from sceneaccess.settings.config import AppConfig
from sceneaccess.storage.session_store import MOMENTS, VideoSessionStore

TEXT = ("- **00:00** She holds a pink toothbrush.\n"
        "- **00:26** In a living room, she stands next to a vacuum cleaner.\n"
        "  Its bin is clear.")


def form(*moments):
    return json.dumps({"moments": [{"start": start, "end": end, "text": text} for start, end, text in moments]})


@pytest.fixture
def session(tmp_path: Path):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"fixture, no media decoding")
    store = VideoSessionStore(tmp_path / "store")
    return store, store.create_session(owner_id="owner", source_path=video, title="A", model="vendor/model",
                                       initial_question="Describe it.")


class Client:
    """Answers each request with the next of answers, an exception is raised; records what was sent."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def run_turn(self, session, messages, run_tool, on_event=None, routing=None, max_tool_rounds=6, on_delta=None):
        self.calls.append({"session": session, "messages": messages, "routing": routing,
                           "max_tool_rounds": max_tool_rounds})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return Turn(entries=[{"role": "assistant", "content": answer}],
                    completion=Completion(answer, "served/model", 7, None, None, finish_reason="stop"))


def test_the_moments_keep_the_answers_words_without_the_marks_the_model_kept():
    found = parsed_moments(form(("00:00", "00:26", "- **00:00** She holds a pink toothbrush."),
                                ("0:26", "1:24", "In a living room, she stands next to a vacuum cleaner. Its bin is "
                                                 "clear.**")), TEXT, 84)
    assert found == [{"start": 0.0, "end": 26.0, "text": "She holds a pink toothbrush."},
                     {"start": 26.0, "end": 84.0,
                      "text": "In a living room, she stands next to a vacuum cleaner. Its bin is clear."}]


INTRO = "A woman with a prosthetic arm stands in a bright room.\n" + TEXT


def test_a_stretch_the_model_left_out_becomes_a_moment_of_its_own_at_its_time():
    found = parsed_moments(form(("00:00", "00:00", "A woman with a prosthetic arm stands in a bright room."),
                                ("00:00", "00:26", "She holds a pink toothbrush.")), INTRO, 84)
    assert [(moment["start"], moment["end"]) for moment in found] == [(0.0, 0.0), (0.0, 26.0), (26.0, 84.0)]
    assert found[2]["text"] == "In a living room, she stands next to a vacuum cleaner. Its bin is clear."


def test_an_opening_the_model_left_out_is_a_moment_from_the_start():
    # As Qwen3.5-9B left out the opening sentence that says she has a prosthesis.
    found = parsed_moments(form(("00:00", "00:26", "She holds a pink toothbrush."),
                                ("00:26", "01:24", "In a living room, she stands next to a vacuum cleaner. Its bin is "
                                                   "clear.")), INTRO, 84)
    assert found[0] == {"start": 0.0, "end": 0.0, "text": "A woman with a prosthetic arm stands in a bright room."}
    assert [moment["start"] for moment in found] == [0.0, 0.0, 26.0]


def test_a_sentence_left_out_inside_a_moment_stays_in_it_in_its_place():
    found = parsed_moments(form(("00:00", "00:26", "She holds a pink toothbrush."),
                                ("00:26", "01:24", "In a living room, she stands next to a vacuum cleaner.")), TEXT, 84)
    assert found[1]["text"] == "In a living room, she stands next to a vacuum cleaner. Its bin is clear."


def test_times_in_brackets_are_read_and_a_short_sentence_said_twice_is_found_at_its_place():
    text = "[00:00] She smiles.\n[00:10] She waves.\n[00:20] She smiles."
    found = parsed_moments(form(("00:00", "00:10", "She smiles."), ("00:10", "00:20", "She waves."),
                                ("00:20", "00:30", "She smiles.")), text, 30)
    assert [(moment["start"], moment["text"]) for moment in found] == [
        (0.0, "She smiles."), (10.0, "She waves."), (20.0, "She smiles.")]


def test_a_time_inside_a_line_leaves_the_time_there_unknown_rather_than_wrong():
    text = "- 0:17 She screws the cap on.\n**Scene 2 (00:26 to 00:40)**\nShe lifts a vacuum cleaner."
    found = parsed_moments(form(("00:17", "00:26", "She screws the cap on."),
                                ("00:26", "00:40", "She lifts a vacuum cleaner.")), text, 84)
    assert [moment["start"] for moment in found] == [17.0, 26.0]


HEADED = ("The video shows a woman with a prosthesis.\n\n"
          "**0:00 - 0:26 (The toothpaste)**\n"
          "*   She holds a toothbrush in her mouth.\n"
          "*   0:17 She screws the cap back on.\n\n"
          "**0:26 - 0:40 (The vacuum cleaner)**\n"
          "*   She lifts a vacuum cleaner.")


def test_a_moment_may_join_lines_under_a_heading_and_loses_the_times_inside_it():
    found = parsed_moments(form(("00:00", "00:26", "The video shows a woman with a prosthesis. She holds a toothbrush "
                                                   "in her mouth. 0:17 She screws the cap back on."),
                                ("00:26", "00:40", "She lifts a vacuum cleaner.")), HEADED, 84)
    assert found[0]["text"] == ("The video shows a woman with a prosthesis. She holds a toothbrush in her mouth. "
                                "She screws the cap back on.")
    # The headings are not read out.
    assert [(moment["start"], moment["text"]) for moment in found][1] == (26.0, "She lifts a vacuum cleaner.")
    assert len(found) == 2


def test_moments_given_the_time_of_another_are_none():
    # The toothpaste given the vacuum cleaner's time, as Qwen3.5-9B shifted all moments by one once.
    assert parsed_moments(form(("00:00", "00:25", "The video shows a woman with a prosthesis."),
                               ("00:26", "00:40", "She holds a toothbrush in her mouth.")), HEADED, 84) is None


@pytest.mark.parametrize("answer", [
    form(("00:00", "00:26", "She holds a blue toothbrush.")),  # a word changed
    form(("00:26", "01:24", "In a living room, she stands next to a vacuum cleaner."),
         ("00:00", "00:26", "She holds a pink toothbrush.")),  # out of order
    form(("00:00", "02:00", "She holds a pink toothbrush.")),  # after the video
    form(("00:30", "00:26", "She holds a pink toothbrush.")),  # ends before it starts
    form(("soon", "00:26", "She holds a pink toothbrush.")),  # no time
    form(),  # nothing with a time
    "She holds a pink toothbrush.",  # not the form
])
def test_moments_that_do_not_keep_to_the_form_are_none(answer):
    assert parsed_moments(answer, TEXT, 84) is None


def test_on_the_own_server_the_step_has_no_tools_no_thinking_and_the_form(session):
    _store, chat = session
    client = Client(form(("00:00", "00:26", "She holds a pink toothbrush.")))
    moments, usage = split_into_moments(client, chat, TEXT, 84, {}, llama_cpp=True)
    assert moments[0]["text"] == "She holds a pink toothbrush." and usage["prompt_tokens"] == 7
    call = client.calls[0]
    assert call["session"].tools == [] and call["max_tool_rounds"] == 0
    assert call["routing"]["response_format"] == FORMAT["response_format"]
    assert call["routing"]["chat_template_kwargs"] == {"enable_thinking": False}
    # Only the text goes, with the video's length to end the last moment.
    assert TEXT in call["messages"][-1]["content"] and "01:24" in call["messages"][-1]["content"]


def test_on_openrouter_only_providers_that_keep_to_the_form_answer_and_thinking_stays(session):
    _store, chat = session
    client = Client(form(("00:00", "00:26", "She holds a pink toothbrush.")))
    split_into_moments(client, chat, TEXT, 84, {"provider": {"ignore": ["Bad"]}}, llama_cpp=False)
    routing = client.calls[0]["routing"]
    assert routing["provider"] == {"ignore": ["Bad"], "require_parameters": True}
    assert "chat_template_kwargs" not in routing


def test_cancelling_stops_the_answer_any_other_failure_only_means_no_moments(session):
    _store, chat = session
    with pytest.raises(RequestCancelled):
        split_into_moments(Client(RequestCancelled()), chat, TEXT, 84, {}, llama_cpp=True)
    assert split_into_moments(Client(RuntimeError("server gone")), chat, TEXT, 84, {}, llama_cpp=True) == (None, None)


def request(chat, client, *, initial=True, llama_cpp=False):
    return answering.Request(
        config=AppConfig(), session=replace(chat, provider="llama.cpp" if llama_cpp else "openrouter"),
        question="Describe it.", initial=initial, client=client, openrouter=None,
        options={"provider": {"ignore": ["Bad"]}, "plugins": [{"id": "auto-router"}]}, search_options={},
        messages=[{"role": "user", "content": "Describe it."}], sectioned=False, duration=84.0, hears=False,
        served_model="", reviewer=None, control=None)


def answered(text=TEXT):
    return Turn(entries=[{"role": "assistant", "content": text, "usage": {"requests": 1, "prompt_tokens": 100}}],
                completion=Completion(text, "vendor/answered", 100, None, None, finish_reason="stop"))


def test_a_first_answer_gets_its_moments_on_its_entry_and_the_step_in_its_usage(session):
    _store, chat = session
    client = Client(form(("00:00", "00:26", "She holds a pink toothbrush.")))
    turn = answering.with_moments(request(chat, client), answered(), None, None)
    # The stretch the model left out is added from its own time.
    assert turn.entries[-1][MOMENTS] == [
        {"start": 0.0, "end": 26.0, "text": "She holds a pink toothbrush."},
        {"start": 26.0, "end": 84.0, "text": "In a living room, she stands next to a vacuum cleaner. Its bin is clear."}]
    assert turn.entries[-1]["usage"]["requests"] == 2 and turn.entries[-1]["usage"]["prompt_tokens"] == 107
    assert turn.completion.requests == 2 and turn.completion.answer == TEXT
    # The model that answered, not the router again, with only the excluded providers.
    call = client.calls[0]
    assert call["session"].model == "vendor/answered" and "plugins" not in call["routing"]


def test_a_cancel_while_splitting_costs_only_the_moments(session):
    _store, chat = session
    turn = answering.with_moments(request(chat, Client(RequestCancelled())), answered(), None, None)
    assert turn.entries[-1]["content"] == TEXT and MOMENTS not in turn.entries[-1]


def test_moments_not_in_the_form_leave_the_answer_as_it_is(session):
    _store, chat = session
    turn = answering.with_moments(request(chat, Client("not the form")), answered(), None, None)
    assert MOMENTS not in turn.entries[-1] and turn.entries[-1]["content"] == TEXT
    # The request was made, so it counts.
    assert turn.entries[-1]["usage"]["requests"] == 2


def test_a_later_question_stays_free_text_without_the_step(session):
    _store, chat = session
    client = Client("A later answer.")
    turn, runner = answering.answer(request(chat, client, initial=False))
    runner.cleanup_files()
    assert len(client.calls) == 1 and MOMENTS not in turn.entries[-1]


def test_the_moments_are_stored_with_the_answer_and_never_sent(session):
    store, chat = session
    moments = [{"start": 0.0, "end": 26.0, "text": "She holds a pink toothbrush."}]
    store.record_turn(chat.id, "owner", [{"role": "assistant", "content": TEXT, MOMENTS: moments}])
    kept = store.get_session(chat.id, "owner")
    assert kept.history[-1][MOMENTS] == moments
    assert MOMENTS not in kept.wire_message(kept.history[-1])
