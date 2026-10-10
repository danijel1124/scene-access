from pathlib import Path
import base64

import pytest

from sceneaccess.session_store import SessionNotFound, VideoSessionStore


def test_create_session_keeps_private_data_url_after_source_is_deleted(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")

    session = store.create_session(
        owner_id="matrix:room-a",
        source_path=source,
        title="Jacket test",
        model="google/gemini-2.5-flash",
        initial_question="What garment is handled?",
    )
    source.unlink()

    restored = store.get_session(session.id, owner_id="matrix:room-a")

    assert restored.source_missing is True
    assert restored.video_data_url == "data:video/mp4;base64," + base64.b64encode(b"fake-mp4-payload").decode("ascii")
    assert restored.messages[0]["cache_control"] == {"type": "ephemeral"}
    assert restored.messages[0]["content"][1]["type"] == "video_url"


def test_follow_up_appends_only_new_question_to_cacheable_history(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(
        owner_id="matrix:room-a", source_path=source, title="Jacket test",
        model="google/gemini-2.5-flash", initial_question="Describe the jacket.",
    )
    store.record_answer(session.id, "matrix:room-a", "It is a black puffer jacket.")

    messages = store.build_follow_up_messages(session.id, "matrix:room-a", "Is it glossy?")

    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][1]["video_url"]["url"].startswith("data:video/mp4;base64,")
    assert messages[-1] == {"role": "user", "content": "Is it glossy?"}


def test_other_owner_cannot_open_session(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(
        owner_id="matrix:room-a", source_path=source, title="Private jacket",
        model="google/gemini-2.5-flash", initial_question="Describe it.",
    )

    with pytest.raises(SessionNotFound):
        store.get_session(session.id, owner_id="matrix:room-b")


def test_deleting_session_removes_persisted_video_context(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(
        owner_id="matrix:room-a", source_path=source, title="Disposable jacket",
        model="google/gemini-2.5-flash", initial_question="Describe it.",
    )
    context_path = store.data_url_path(session.id)
    assert context_path.exists()

    store.delete_session(session.id, owner_id="matrix:room-a")

    assert not context_path.exists()
    with pytest.raises(SessionNotFound):
        store.get_session(session.id, owner_id="matrix:room-a")


def test_list_sessions_returns_most_recent_first(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    first = store.create_session(owner_id="matrix:room-a", source_path=source, title="First", model="google/gemini-2.5-flash", initial_question="First?")
    second = store.create_session(owner_id="matrix:room-a", source_path=source, title="Second", model="google/gemini-2.5-flash", initial_question="Second?")

    assert [item.id for item in store.list_sessions("matrix:room-a")] == [second.id, first.id]


def test_record_follow_up_persists_question_and_answer(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="matrix:room-a", source_path=source, title="Jacket", model="google/gemini-2.5-flash", initial_question="Describe it.")

    store.record_follow_up(session.id, "matrix:room-a", "Is it glossy?", "No, it is matte.")

    assert [message["content"] for message in store.get_session(session.id, "matrix:room-a").history] == ["Is it glossy?", "No, it is matte."]


def test_update_source_path_keeps_the_existing_video_context(tmp_path: Path):
    original = tmp_path / "old.mp4"
    replacement = tmp_path / "new.mp4"
    original.write_bytes(b"fake-mp4-payload")
    replacement.write_bytes(b"another-file")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="matrix:room-a", source_path=original, title="Jacket", model="google/gemini-2.5-flash", initial_question="Describe it.")

    store.update_source_path(session.id, "matrix:room-a", replacement)

    restored = store.get_session(session.id, "matrix:room-a")
    assert restored.source_path == str(replacement.resolve())
    assert restored.video_data_url.endswith(base64.b64encode(b"fake-mp4-payload").decode("ascii"))
