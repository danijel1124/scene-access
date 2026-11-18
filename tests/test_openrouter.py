import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from sceneaccess.openrouter import OpenRouterClient, OpenRouterError
from sceneaccess.session_store import VideoSessionStore
from sceneaccess.tools import ToolResult, tool_definitions


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_client_sends_stable_session_id_and_returns_cache_usage(tmp_path: Path):
    video = tmp_path / "jacket.mp4"
    video.write_bytes(b"fake-mp4-payload")
    session = VideoSessionStore(tmp_path / "data").create_session(
        owner_id="local", source_path=video, title="Jacket", model="google/gemini-2.5-flash",
        initial_question="Describe it.",
    )
    seen = {}

    def opener(request, timeout):
        seen["body"] = json.loads(request.data)
        return FakeResponse({
            "model": "z-ai/glm-4.5v",
            "provider": "CoreWeave",
            "choices": [{"message": {"content": "A puffer jacket."}}],
            "usage": {"prompt_tokens": 42, "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 2}},
        })

    routing = {"provider": {"ignore": ["novita"]}, "plugins": [{"id": "auto-router", "excluded_models": ["a/b"]}]}
    result = OpenRouterClient("test-key", base_url="https://router.example/chat", opener=opener).run_turn(
        session, session.messages, lambda *_: None, routing=routing).completion

    assert seen["body"]["session_id"] == session.id
    assert seen["body"]["provider"] == routing["provider"] and seen["body"]["plugins"] == routing["plugins"]
    assert result.answer == "A puffer jacket."
    assert result.model == "z-ai/glm-4.5v"
    assert result.provider == "CoreWeave"
    assert result.cached_tokens == 40
    assert result.cache_write_tokens == 2


def test_client_keeps_missing_cache_details_distinct_from_zero(tmp_path: Path):
    video = tmp_path / "jacket.mp4"
    video.write_bytes(b"fake-mp4-payload")
    session = VideoSessionStore(tmp_path / "data").create_session(
        owner_id="local", source_path=video, title="Jacket", model="openrouter/auto",
        initial_question="Describe it.",
    )

    def opener(request, timeout):
        return FakeResponse({"choices": [{"message": {"content": "A jacket."}}], "usage": {"prompt_tokens": 42}})

    result = OpenRouterClient("test-key", opener=opener).run_turn(session, session.messages, lambda *_: None).completion

    assert result.model == "openrouter/auto"
    assert result.cached_tokens is None
    assert result.cache_write_tokens is None


def _tool_session(tmp_path: Path):
    video = tmp_path / "jacket.mp4"
    video.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "data")
    session = store.create_session(
        owner_id="local", source_path=video, title="Jacket", model="openrouter/auto", initial_question="Brand?",
        system_prompt="Describe for a blind person.", tools=tool_definitions(online_video=False),
    )
    return store, session


def test_run_turn_runs_tool_calls_and_shows_images_until_the_model_answers(tmp_path: Path):
    store, session = _tool_session(tmp_path)
    (session.directory / "frame_1.jpg").write_bytes(b"jpeg-bytes")
    bodies = []
    replies = [
        {"model": "m", "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "zoom_frame", "arguments": "{\"time\": \"02:51\"}"}}]}}],
         "usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 90, "cache_write_tokens": 0}}},
        {"model": "m", "choices": [{"message": {"role": "assistant", "content": "A Moncler logo.", "annotations": [
            {"type": "url_citation", "url_citation": {"url": "https://example.com/a", "title": "A"}}]}}],
         "usage": {"prompt_tokens": 120, "prompt_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 5}}},
    ]

    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        return FakeResponse(replies[len(bodies) - 1])

    calls, events = [], []

    def run_tool(name, arguments):
        calls.append((name, json.loads(arguments)))
        return ToolResult("Image of 02:51 follows.", image="frame_1.jpg", caption="Image of 02:51")

    turn = OpenRouterClient("k", opener=opener).run_turn(session, session.messages, run_tool,
                                                         lambda kind, text: events.append((kind, text)))

    assert calls == [("zoom_frame", {"time": "02:51"})]
    assert events == [("tool_call", "Zoom into 02:51"), ("tool_result", "Image of 02:51")]
    assert [entry["role"] for entry in turn.entries] == ["assistant", "tool", "user", "assistant"]
    # The tools are sent with every request, and the second one carries the call, its result and the image.
    assert all(body["tools"] == session.tools for body in bodies)
    second = bodies[1]["messages"]
    assert second[0] == {"role": "system", "content": "Describe for a blind person."}
    assert second[-3]["tool_calls"][0]["id"] == "call_1"
    assert second[-2] == {"role": "tool", "tool_call_id": "call_1", "content": "Image of 02:51 follows."}
    assert second[-1]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,anBlZy1ieXRlcw=="
    answer = turn.entries[-1]
    assert answer["content"] == "A Moncler logo."
    assert answer["sources"] == [{"title": "A", "url": "https://example.com/a"}]
    assert answer["usage"]["prompt_tokens"] == 220 and answer["usage"]["cached_tokens"] == 190
    assert answer["usage"]["requests"] == 2
    # Stored images keep a file reference, not the image data.
    assert turn.entries[2]["content"][1]["image_url"]["url"] == "session-file:frame_1.jpg"


def test_run_turn_stops_when_a_model_never_stops_calling_tools(tmp_path: Path):
    from sceneaccess.openrouter import MAX_TOOL_ROUNDS

    _store, session = _tool_session(tmp_path)
    bodies = []

    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        return FakeResponse({"model": "m", "usage": {}, "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{len(bodies)}", "type": "function", "function": {"name": "video_information", "arguments": "{}"}}]}}]})

    ran = []
    turn = OpenRouterClient("k", opener=opener).run_turn(
        session, session.messages, lambda name, arguments: ran.append(name) or ToolResult("info"))

    assert len(bodies) == MAX_TOOL_ROUNDS + 1
    assert bodies[-1]["tool_choice"] == "none"
    assert len(ran) == MAX_TOOL_ROUNDS
    assert "tool_calls" not in turn.entries[-1]
    assert "kept calling tools" in turn.completion.answer


def test_run_turn_answers_without_tools_when_the_model_rejects_them(tmp_path: Path):
    _store, session = _tool_session(tmp_path)
    bodies = []

    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        if "tools" in bodies[-1]:
            body = json.dumps({"error": {"message": "No endpoints found that support tool use."}}).encode()
            raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(body))
        return FakeResponse({"model": "m", "choices": [{"message": {"content": "Plain answer."}}], "usage": {}})

    turn = OpenRouterClient("k", opener=opener).run_turn(session, session.messages, lambda *_: None)

    assert turn.completion.answer == "Plain answer."
    assert turn.completion.tools_unavailable is True
    assert "tools" not in bodies[-1]


def test_transcription_asks_for_word_times_next_to_the_chat_endpoint():
    seen = {}
    words = {"text": "Hello.", "words": [{"word": "Hello.", "start": 0.5, "end": 0.9}]}

    def opener(request, timeout):
        seen["url"], seen["body"] = request.full_url, json.loads(request.data)
        seen["authorization"] = request.get_header("Authorization")
        return FakeResponse(words)

    client = OpenRouterClient("test-key", base_url="https://router.example/api/v1/chat/completions", opener=opener)
    assert client.transcribe(b"audio bytes", "mp3") == words
    assert seen["url"] == "https://router.example/api/v1/audio/transcriptions"
    assert seen["authorization"] == "Bearer test-key"
    assert seen["body"] == {"model": "elevenlabs/scribe-v2",
                            "input_audio": {"data": "YXVkaW8gYnl0ZXM=", "format": "mp3"},
                            "response_format": "verbose_json", "timestamp_granularities": ["word", "segment"]}


def test_an_error_in_a_successful_response_is_reported(tmp_path: Path):
    _store, session = _tool_session(tmp_path)

    def opener(request, timeout):
        return FakeResponse({"error": {"message": "Rate limit exceeded", "code": 429}})

    with pytest.raises(OpenRouterError, match="Rate limit exceeded") as raised:
        OpenRouterClient("k", opener=opener).run_turn(session, session.messages, lambda *_: None)
    assert raised.value.status == 429


def test_a_pause_during_the_final_answer_still_completes(tmp_path: Path):
    import threading
    from sceneaccess.request_control import RequestControl
    _store, session = _tool_session(tmp_path)
    control = RequestControl()

    def opener(request, timeout):
        control.pause()  # The user presses Pause while the model writes its final answer.
        return FakeResponse({"choices": [{"message": {"content": "Done"}}]})

    answers = []
    worker = threading.Thread(target=lambda: answers.append(OpenRouterClient("k", opener=opener, control=control)
                                                            .run_turn(session, session.messages, lambda *_: None)))
    worker.start()
    worker.join(3)
    try:
        assert not worker.is_alive(), "a pause after the final answer must not hold the request"
        assert answers[0].completion.answer == "Done"
    finally:
        control.cancel()
        worker.join(2)


def test_http_errors_show_the_providers_reason(tmp_path: Path):
    _store, session = _tool_session(tmp_path)

    def opener(request, timeout):
        body = json.dumps({"error": {"message": "Provider returned error", "metadata": {"raw": "File too large"}}}).encode()
        raise HTTPError(request.full_url, 500, "Internal Server Error", {}, io.BytesIO(body))

    with pytest.raises(OpenRouterError, match=r"HTTP 500: Provider returned error \(File too large\)"):
        OpenRouterClient("k", opener=opener).run_turn(session, session.messages, lambda *_: None)


@pytest.mark.parametrize("limit", [0, 1, 9])
def test_configured_round_limit_controls_tool_execution(tmp_path, limit):
    _store, session = _tool_session(tmp_path)
    bodies, ran = [], []
    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        return FakeResponse({"choices": [{"message": {"content": None, "tool_calls": [
            {"id": f"c{len(bodies)}", "function": {"name": "zoom_frame", "arguments": "{}"}}]}}]})
    turn = OpenRouterClient("k", opener=opener).run_turn(
        session, session.messages, lambda *args: ran.append(True) or ToolResult("fixture"), max_tool_rounds=limit)
    assert len(ran) == limit
    assert len(bodies) == limit + 1
    if limit == 0:
        assert "tools" not in bodies[-1]
    else:
        assert bodies[-1]["tool_choice"] == "none"
    assert str(limit) in turn.completion.answer


def test_cancel_during_response_prevents_tool_execution(tmp_path):
    from sceneaccess.request_control import RequestControl, RequestCancelled
    _store, session = _tool_session(tmp_path)
    control = RequestControl()
    def opener(request, timeout):
        control.cancel()
        return FakeResponse({"choices": [{"message": {"tool_calls": [
            {"id": "x", "function": {"name": "zoom_frame", "arguments": "{}"}}]}}]})
    ran = []
    with pytest.raises(RequestCancelled):
        OpenRouterClient("k", opener=opener, control=control).run_turn(
            session, session.messages, lambda *args: ran.append(True) or ToolResult("fixture"))
    assert ran == []


def test_pause_finishes_tools_then_resumes_without_repeating_them(tmp_path):
    import threading
    from sceneaccess.request_control import RequestControl
    _store, session = _tool_session(tmp_path)
    control = RequestControl()
    paused = threading.Event()
    bodies, ran, answers = [], [], []
    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        if len(bodies) == 1:
            control.pause()
            return FakeResponse({"choices": [{"message": {"tool_calls": [
                {"id": "x", "function": {"name": "zoom_frame", "arguments": "{}"}}]}}]})
        return FakeResponse({"choices": [{"message": {"content": "Done"}}]})
    def run():
        answers.append(OpenRouterClient("k", opener=opener, control=control).run_turn(
            session, session.messages, lambda *args: ran.append(True) or ToolResult("fixture"),
            lambda kind, text: paused.set() if kind == "paused" else None))
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert paused.wait(2)
        assert ran == [True] and len(bodies) == 1
        control.resume()
        worker.join(2)
        assert not worker.is_alive()
        assert ran == [True] and len(bodies) == 2
        assert answers[0].completion.answer == "Done"
    finally:
        control.cancel()
        worker.join(2)


class StreamResponse:
    """A streamed answer: one server-sent event per chunk, as llama.cpp and OpenRouter send them."""
    def __init__(self, chunks, done=True):
        self.lines = [f"data: {json.dumps(chunk)}\n\n".encode("utf-8") for chunk in chunks]
        self.lines += [b"data: [DONE]\n\n"] if done else []

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _delta(**fields):
    return {"choices": [{"delta": fields}]}


def test_a_streamed_answer_is_reported_as_it_comes_and_put_together(tmp_path: Path):
    _store, session = _tool_session(tmp_path)
    bodies, pieces = [], []

    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        # llama.cpp calls the reasoning reasoning_content, OpenRouter reasoning; the usage comes last, alone.
        return StreamResponse([{"model": "m", **_delta(reasoning_content="Look at ")}, _delta(reasoning="the logo."),
                               _delta(content="A Moncler "), _delta(content="logo."),
                               {"choices": [], "usage": {"prompt_tokens": 50}}])

    turn = OpenRouterClient("k", opener=opener).run_turn(session, session.messages, lambda *_: None,
                                                         on_delta=lambda kind, text: pieces.append((kind, text)))
    assert bodies[0]["stream"] is True
    assert pieces == [("thinking", "Look at "), ("thinking", "the logo."), ("text", "A Moncler "), ("text", "logo.")]
    answer = turn.entries[-1]
    assert answer["content"] == turn.completion.answer == "A Moncler logo."
    # The thinking is kept for the Chat history; the usage of the last chunk counts.
    assert answer["thinking"] == "Look at the logo." and turn.completion.prompt_tokens == 50


def test_streamed_tool_calls_and_reasoning_go_back_as_without_streaming(tmp_path: Path):
    _store, session = _tool_session(tmp_path)
    (session.directory / "frame_1.jpg").write_bytes(b"jpeg-bytes")
    bodies = []
    rounds = [
        [_delta(reasoning="Zoom in.", reasoning_details=[{"type": "reasoning.text", "index": 0, "text": "Zoom ",
                                                          "signature": None}]),
         _delta(reasoning_details=[{"type": "reasoning.text", "index": 0, "text": "in.", "signature": "sig-1"}]),
         # The call comes in pieces: its name, then its arguments bit by bit.
         _delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function", "function": {"name": "zoom_frame"}}]),
         _delta(tool_calls=[{"index": 0, "function": {"arguments": "{\"time\""}}]),
         _delta(tool_calls=[{"index": 0, "function": {"arguments": ": \"02:51\"}"}}])],
        [_delta(content="A Moncler logo.", annotations=[
            {"type": "url_citation", "url_citation": {"url": "https://example.com/a", "title": "A"}}])],
    ]

    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        return StreamResponse(rounds[len(bodies) - 1])

    calls = []

    def run_tool(name, arguments):
        calls.append((name, json.loads(arguments)))
        return ToolResult("Image follows.", image="frame_1.jpg", caption="Image of 02:51")

    turn = OpenRouterClient("k", opener=opener).run_turn(session, session.messages, run_tool, on_delta=lambda *_: None)
    assert calls == [("zoom_frame", {"time": "02:51"})]
    sent = bodies[1]["messages"][-3]
    assert sent["tool_calls"] == [{"id": "call_1", "type": "function",
                                   "function": {"name": "zoom_frame", "arguments": "{\"time\": \"02:51\"}"}}]
    # The signature that came after a first piece without one goes back, as Gemini needs it.
    assert sent["reasoning_details"] == [{"type": "reasoning.text", "index": 0, "text": "Zoom in.", "signature": "sig-1"}]
    # The readable copy of the thinking is for the Chat history only.
    assert "thinking" not in sent and "reasoning" not in sent and turn.entries[0]["thinking"] == "Zoom in."
    assert turn.entries[-1]["sources"] == [{"title": "A", "url": "https://example.com/a"}]


def test_an_answer_cut_off_at_the_token_limit_says_so(tmp_path: Path):
    _store, session = _tool_session(tmp_path)
    # llama.cpp sends why the answer ended in the last chunk with a choice; "length" means it was cut off.
    cut = [_delta(reasoning="Wait, let me check again."), {"choices": [{"delta": {}, "finish_reason": "length"}]}]
    turn = OpenRouterClient("k", opener=lambda request, timeout: StreamResponse(cut)).run_turn(
        session, session.messages, lambda *_: None, on_delta=lambda *_: None)
    assert turn.completion.cut_off and turn.completion.answer == ""
    done = [_delta(content="A jacket."), {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
    turn = OpenRouterClient("k", opener=lambda request, timeout: StreamResponse(done)).run_turn(
        session, session.messages, lambda *_: None, on_delta=lambda *_: None)
    assert not turn.completion.cut_off and turn.completion.finish_reason == "stop"


def test_a_server_that_does_not_stream_still_gives_the_whole_answer(tmp_path: Path):
    _store, session = _tool_session(tmp_path)

    class Unstreamed(StreamResponse):
        def __init__(self, payload):
            self.lines = [line.encode("utf-8") for line in json.dumps(payload, indent=2).splitlines(keepends=True)]

    answer = {"model": "m", "choices": [{"message": {"content": "A jacket."}}], "usage": {"prompt_tokens": 7}}
    turn = OpenRouterClient("k", opener=lambda request, timeout: Unstreamed(answer)).run_turn(
        session, session.messages, lambda *_: None, on_delta=lambda *_: None)
    assert turn.completion.answer == "A jacket." and turn.completion.prompt_tokens == 7


def test_an_error_in_the_stream_fails_the_request(tmp_path: Path):
    _store, session = _tool_session(tmp_path)
    opener = lambda request, timeout: StreamResponse([_delta(content="Half"), {"error": {"message": "overloaded"}}])
    with pytest.raises(OpenRouterError, match="overloaded"):
        OpenRouterClient("k", opener=opener).run_turn(session, session.messages, lambda *_: None,
                                                      on_delta=lambda *_: None)


@pytest.fixture
def stream_server():
    """A local server streaming an answer slowly, piece by piece; hold makes it wait before the last piece."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hold = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in (_delta(reasoning_content="Thinking."), _delta(content="Half "), _delta(content="done.")):
                if chunk["choices"][0]["delta"].get("content") == "done.":
                    hold.wait(10)
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.05)
            # As llama.cpp ends an answer stopped at the token limit.
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'length'}]})}\n\n"
                             .encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions", hold
    hold.set()
    server.shutdown()


def test_a_request_that_can_be_cancelled_streams_through_its_worker_process(tmp_path: Path, stream_server):
    from sceneaccess.request_control import RequestControl
    _store, session = _tool_session(tmp_path)
    url, hold = stream_server
    hold.set()
    pieces = []
    turn = OpenRouterClient("", base_url=url, control=RequestControl()).run_turn(
        session, session.messages, lambda *_: None, on_delta=lambda kind, text: pieces.append((kind, text)))
    assert pieces == [("thinking", "Thinking."), ("text", "Half "), ("text", "done.")]
    assert turn.completion.answer == "Half done."
    # Why the answer ended comes through the worker process too, the way the app sends.
    assert turn.completion.cut_off


def test_cancelling_a_streamed_request_stops_it_at_once(tmp_path: Path, stream_server):
    from sceneaccess.request_control import RequestCancelled, RequestControl
    _store, session = _tool_session(tmp_path)
    url, _hold = stream_server
    control = RequestControl()

    def piece(kind, text):
        if text == "Half ":
            control.cancel()

    with pytest.raises(RequestCancelled):
        OpenRouterClient("", base_url=url, control=control).run_turn(session, session.messages, lambda *_: None,
                                                                     on_delta=piece)
