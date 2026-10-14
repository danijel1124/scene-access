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

    result = OpenRouterClient("test-key", base_url="https://router.example/chat", opener=opener).run_turn(
        session, session.messages, lambda *_: None).completion

    assert seen["body"]["session_id"] == session.id
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


def test_http_errors_show_the_providers_reason(tmp_path: Path):
    _store, session = _tool_session(tmp_path)

    def opener(request, timeout):
        body = json.dumps({"error": {"message": "Provider returned error", "metadata": {"raw": "File too large"}}}).encode()
        raise HTTPError(request.full_url, 500, "Internal Server Error", {}, io.BytesIO(body))

    with pytest.raises(OpenRouterError, match=r"HTTP 500: Provider returned error \(File too large\)"):
        OpenRouterClient("k", opener=opener).run_turn(session, session.messages, lambda *_: None)
