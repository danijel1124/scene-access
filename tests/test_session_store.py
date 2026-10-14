from pathlib import Path
import base64
import json
import sqlite3

import pytest

from sceneaccess.session_store import SessionNotFound, VideoSessionStore


def test_chats_from_the_first_release_still_open_after_the_new_columns_are_added(tmp_path: Path):
    root = tmp_path / "chats"
    (root / "sessions" / "vid_old").mkdir(parents=True)
    (root / "sessions" / "vid_old" / "context.data-url").write_text("data:video/mp4;base64,AA==", encoding="ascii")
    with sqlite3.connect(root / "sceneaccess.db") as conn:
        conn.execute("""CREATE TABLE sessions (id TEXT PRIMARY KEY, owner_id TEXT, title TEXT, source_path TEXT,
            model TEXT, initial_question TEXT, history_json TEXT)""")
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("vid_old", "local", "Old", "C:/gone.mp4", "m", "Describe.", json.dumps([{"role": "assistant", "content": "Old answer."}])))

    session = VideoSessionStore(root).get_session("vid_old", "local")

    assert session.history == [{"role": "assistant", "content": "Old answer."}]
    assert session.system_prompt == "" and session.tools == [] and session.source_url == "" and session.video_info is None
    # Without a system prompt the request starts with the video, exactly as before, so its cache still matches.
    assert session.messages[0]["role"] == "user"


def test_chats_from_the_tools_release_get_no_transcript_so_their_cache_stays_valid(tmp_path: Path):
    root = tmp_path / "chats"
    (root / "sessions" / "vid_t").mkdir(parents=True)
    (root / "sessions" / "vid_t" / "context.data-url").write_text("data:video/mp4;base64,AA==", encoding="ascii")
    with sqlite3.connect(root / "sceneaccess.db") as conn:
        conn.execute("""CREATE TABLE sessions (id TEXT PRIMARY KEY, owner_id TEXT, title TEXT, source_path TEXT,
            model TEXT, initial_question TEXT, history_json TEXT, system_prompt TEXT NOT NULL DEFAULT '',
            tools_json TEXT NOT NULL DEFAULT '[]', source_url TEXT NOT NULL DEFAULT '',
            video_info_json TEXT NOT NULL DEFAULT '')""")
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("vid_t", "o", "T", "C:/gone.mp4", "m", "?", "[]", "Base.", "[]", "https://youtu.be/x", ""))

    session = VideoSessionStore(root).get_session("vid_t", "o")

    assert session.system_prompt == "Base." and session.source_url == "https://youtu.be/x"
    assert session.transcript_state == "none" and session.transcript == "" and session.audio_only is False
    assert [part["type"] for part in session.messages[1]["content"]] == ["text", "video_url"]


def test_the_transcript_is_sent_before_the_video_so_it_is_cached_with_it(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="o", source_path=source, title="J", model="m", initial_question="Brand?",
                                   transcript_state="pending", audio_only=True)
    assert session.transcript_state == "pending" and session.audio_only is True

    store.update_transcript(session.id, "o", "done", "[00:01] Hello.")
    content = store.get_session(session.id, "o").messages[0]["content"]

    assert [part["type"] for part in content] == ["text", "text", "video_url"]
    assert content[1]["text"].endswith("[00:01] Hello.") and "may contain errors" in content[1]["text"]
    assert "cache_control" in content[2]


def test_changing_the_setup_decides_about_the_transcript_again_and_a_link_can_be_added(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="o", source_path=source, title="J", model="m", initial_question="?")
    store.update_transcript(session.id, "o", "failed")

    store.update_source(session.id, "o", "https://www.youtube.com/watch?v=jNQXAC9IVRw", {"title": "Me at the zoo"})
    store.update_setup(session.id, "o", "Base.", [{"type": "openrouter:web_search"}], audio_only=True)
    session = store.get_session(session.id, "o")

    assert session.source_url.endswith("jNQXAC9IVRw") and session.video_info == {"title": "Me at the zoo"}
    assert session.transcript_state == "pending" and session.transcript == "" and session.audio_only is True


def test_messages_keep_the_tool_protocol_and_drop_local_fields(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="o", source_path=source, title="J", model="m", initial_question="Brand?",
                                   system_prompt="Base.", tools=[{"type": "openrouter:web_search"}],
                                   source_url="https://youtu.be/x", video_info={"title": "J"})
    (session.directory / "frame_a.jpg").write_bytes(b"img")
    store.record_turn(session.id, "o", [
        {"role": "assistant", "content": None, "reasoning_details": [{"type": "x"}],
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "zoom_frame", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "Image follows.", "caption": "Image of 00:00"},
        {"role": "user", "content": [{"type": "text", "text": "Image follows."},
                                     {"type": "image_url", "image_url": {"url": "session-file:frame_a.jpg"}}]},
        {"role": "assistant", "content": "Done.", "usage": {"prompt_tokens": 1}, "sources": [{"title": "t", "url": "u"}]},
    ])

    session = store.get_session(session.id, "o")
    messages = session.messages

    assert session.tools == [{"type": "openrouter:web_search"}] and session.video_info == {"title": "J"}
    assert messages[0] == {"role": "system", "content": "Base."}
    assert messages[2]["reasoning_details"] == [{"type": "x"}] and messages[2]["tool_calls"][0]["id"] == "c1"
    assert messages[3] == {"role": "tool", "tool_call_id": "c1", "content": "Image follows."}
    assert messages[4]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"img").decode()
    assert messages[5] == {"role": "assistant", "content": "Done."}


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
    assert restored.messages[0]["content"][1]["type"] == "video_url"
    assert restored.messages[0]["content"][1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in restored.messages[0]


def test_follow_up_appends_only_new_question_to_cacheable_history(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(
        owner_id="matrix:room-a", source_path=source, title="Jacket test",
        model="google/gemini-2.5-flash", initial_question="Describe the jacket.",
    )
    store.record_turn(session.id, "matrix:room-a", [{"role": "assistant", "content": "It is a black puffer jacket."}])

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


def test_record_turn_appends_every_entry_of_a_turn(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="matrix:room-a", source_path=source, title="Jacket", model="google/gemini-2.5-flash", initial_question="Describe it.")

    store.record_turn(session.id, "matrix:room-a", [{"role": "assistant", "content": "A jacket."}])
    store.record_turn(session.id, "matrix:room-a", [{"role": "user", "content": "Is it glossy?"}, {"role": "assistant", "content": "No, it is matte."}])

    assert [message["content"] for message in store.get_session(session.id, "matrix:room-a").history] == ["A jacket.", "Is it glossy?", "No, it is matte."]


def test_reassign_owner_moves_legacy_chats_to_the_new_owner(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="local:user", source_path=source, title="Jacket", model="openrouter/auto", initial_question="Describe it.")

    store.reassign_owner("local:user", "local:danijel")

    assert [item.id for item in store.list_sessions("local:danijel")] == [session.id]
    assert store.list_sessions("local:user") == []


def test_failed_video_read_leaves_no_session_behind(tmp_path: Path, monkeypatch):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")

    def fail(_self):
        raise MemoryError

    monkeypatch.setattr(Path, "read_bytes", fail)
    with pytest.raises(MemoryError):
        store.create_session(owner_id="local", source_path=source, title="Jacket", model="openrouter/auto", initial_question="Describe it.")

    assert list(store.sessions_dir.iterdir()) == []
    assert store.list_sessions("local") == []


def test_recorded_usage_is_kept_locally_but_not_sent_to_the_provider(tmp_path: Path):
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="matrix:room-a", source_path=source, title="Jacket", model="openrouter/auto", initial_question="Describe it.")
    usage = {"model": "z-ai/glm-4.5v", "prompt_tokens": 42, "cached_tokens": 40, "cache_write_tokens": 0}

    store.record_turn(session.id, "matrix:room-a", [{"role": "assistant", "content": "A jacket.", "usage": usage}])

    assert store.get_session(session.id, "matrix:room-a").history[0]["usage"] == usage
    messages = store.build_follow_up_messages(session.id, "matrix:room-a", "Is it glossy?")
    assert messages[1] == {"role": "assistant", "content": "A jacket."}


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


def test_a_smaller_copy_is_sent_while_the_original_stays_the_source(tmp_path: Path):
    original, small = tmp_path / "big.mp4", tmp_path / "small.mp4"
    original.write_bytes(b"x" * 300)
    small.write_bytes(b"y" * 30)
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="local", source_path=original, title="T", model="", initial_question="?",
                                   sent_video=small)
    assert session.source_path == str(original.resolve()) and session.model == ""
    assert session.video_data_url.endswith(base64.b64encode(b"y" * 30).decode("ascii"))
    assert session.sent_video_size == len("data:video/mp4;base64,") + 40

    assert session.size_confirmed is False
    store.confirm_size(session.id, "local")
    assert store.get_session(session.id, "local").size_confirmed is True
    smaller = tmp_path / "smaller.mp4"
    smaller.write_bytes(b"z" * 3)
    store.replace_video(session.id, "local", smaller)
    replaced = store.get_session(session.id, "local")
    # Another video: its size has not been confirmed yet.
    assert replaced.video_data_url == "data:video/mp4;base64,enp6" and replaced.size_confirmed is False
    store.confirm_size(session.id, "local")
    store.update_chat_settings(session.id, "local", "a/one", False)
    assert store.get_session(session.id, "local").size_confirmed is False


def test_chat_settings_change_the_model_and_decide_about_the_transcript_again(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="local", source_path=video, title="T", model="", initial_question="?")
    store.update_chat_settings(session.id, "local", "z-ai/glm-flash", False)
    changed = store.get_session(session.id, "local")
    # The new model may not hear the video, so the next question checks whether it needs a transcript.
    assert (changed.model, changed.audio_only, changed.transcript_state) == ("z-ai/glm-flash", False, "pending")
    store.update_transcript(session.id, "local", "done", "[00:01] Hello")
    store.update_chat_settings(session.id, "local", "", True)
    kept = store.get_session(session.id, "local")
    assert (kept.model, kept.audio_only, kept.transcript_state, kept.transcript) == ("", True, "done", "[00:01] Hello")
