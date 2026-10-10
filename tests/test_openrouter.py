import json
from pathlib import Path

from sceneaccess.openrouter import OpenRouterClient
from sceneaccess.session_store import VideoSessionStore


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
            "choices": [{"message": {"content": "A puffer jacket."}}],
            "usage": {"prompt_tokens": 42, "cached_tokens": 40},
        })

    result = OpenRouterClient("test-key", base_url="https://router.example/chat", opener=opener).complete(session, session.messages)

    assert seen["body"]["session_id"] == session.id
    assert result.answer == "A puffer jacket."
    assert result.cached_tokens == 40
