from pathlib import Path
import base64
import json
import os
import sqlite3

import pytest

from sceneaccess.session_store import SessionNotFound, VideoSessionStore, round_bounds


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


@pytest.mark.parametrize("state", ["done", "appended"])
def test_changing_the_setup_keeps_a_finished_or_added_transcript(tmp_path: Path, state):
    # Making it again would be paid twice, and an added one would then reach the model twice.
    source = tmp_path / "jacket.mp4"
    source.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="o", source_path=source, title="J", model="m", initial_question="?")
    store.update_transcript(session.id, "o", state, "[00:01] Hello" if state == "done" else "")
    store.update_setup(session.id, "o", "Base.", [])
    kept = store.get_session(session.id, "o")
    assert kept.transcript_state == state and kept.transcript == ("[00:01] Hello" if state == "done" else "")


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


@pytest.mark.parametrize("fail_on", [1, 2])
def test_failed_session_unlink_keeps_chat_registered_for_retry(tmp_path: Path, monkeypatch, fail_on):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"private video")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="local", source_path=source, title="Delete", model="", initial_question="?")
    (session.directory / "frame_a.jpg").write_bytes(b"private frame")
    original_unlink = Path.unlink
    calls = []

    def fail_unlink(path, *args, **kwargs):
        if path.parent == session.directory:
            calls.append(path)
            # A separate reader must still see the chat until its files are removed.
            assert store.get_session(session.id, "local").id == session.id
            if len(calls) == fail_on:
                raise PermissionError("injected unlink failure")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(PermissionError, match="injected unlink failure"):
            store.delete_session(session.id, "local")

    assert [item.id for item in store.list_sessions("local")] == [session.id]
    assert calls[-1].exists()
    if fail_on == 2:
        assert not calls[0].exists()
    store.delete_session(session.id, "local")
    assert store.list_sessions("local") == []
    assert not session.directory.exists()
    assert source.read_bytes() == b"private video"


def link_directory(link: Path, target: Path, kind: str) -> None:
    """Creates a directory symlink or, on Windows, a junction, which needs no special rights."""
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("junctions exist only on Windows")
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("creating symlinks on Windows needs Developer Mode or admin rights")
        raise


@pytest.mark.parametrize("unexpected", ["symlink", "junction", "subdirectory"])
def test_delete_refuses_unexpected_directories_without_removing_private_files(tmp_path: Path, unexpected):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"private video")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="local", source_path=source, title="Keep", model="", initial_question="?")
    if unexpected in ("symlink", "junction"):
        target = tmp_path / "unrelated"
        session.directory.rename(target)
        link_directory(session.directory, target, unexpected)
    else:
        target = session.directory
        (target / "unrelated").mkdir()
        (target / "unrelated" / "keep.txt").write_text("keep")

    with pytest.raises(OSError):
        store.delete_session(session.id, "local")

    assert (target / "context.data-url").is_file()
    assert store.get_session(session.id, "local").id == session.id


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


def test_failed_insert_removes_only_the_new_private_session_files(tmp_path: Path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"private video")
    store = VideoSessionStore(tmp_path / "app-data")
    existing = store.create_session(owner_id="local", source_path=source, title="Keep", model="", initial_question="?")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("""CREATE TRIGGER reject_session BEFORE INSERT ON sessions
                        BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END""")

    with pytest.raises(sqlite3.IntegrityError, match="injected insert failure"):
        store.create_session(owner_id="local", source_path=source, title="Fail", model="", initial_question="?")

    assert list(store.sessions_dir.iterdir()) == [existing.directory]
    assert existing.video_data_url.endswith(base64.b64encode(b"private video").decode("ascii"))
    assert [session.id for session in store.list_sessions("local")] == [existing.id]
    assert source.read_bytes() == b"private video"


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


def chat_with_two_rounds(tmp_path: Path):
    """History: 0 tool call, 1 tool result, 2 image, 3 first answer, 4 second question, 5 second answer."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="o", source_path=video, title="T", model="", initial_question="First?")
    (store.sessions_dir / session.id / "frame_a.jpg").write_bytes(b"jpg")
    call = {"id": "c1", "type": "function", "function": {"name": "zoom_frame", "arguments": "{}"}}
    store.record_turn(session.id, "o", [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "c1", "content": "Image follows."},
        {"role": "user", "content": [{"type": "text", "text": "Image of 00:01"},
                                     {"type": "image_url", "image_url": {"url": "session-file:frame_a.jpg"}}]},
        {"role": "assistant", "content": "First answer."}])
    store.record_turn(session.id, "o", [{"role": "user", "content": "Second?"},
                                        {"role": "assistant", "content": "Second answer."}])
    return store, session


def texts(messages):
    return [part.get("text") if isinstance(part, dict) else part for message in messages
            for part in (message["content"] if isinstance(message["content"], list) else [message["content"]])]


def test_a_round_reaches_from_its_question_to_its_answer(tmp_path: Path):
    store, session = chat_with_two_rounds(tmp_path)
    history = store.get_session(session.id, "o").history
    # The first round has no question of its own: it starts with the chat.
    assert [round_bounds(history, index) for index in range(6)] == [(0, 3)] * 4 + [(4, 5)] * 2


def test_a_round_out_of_the_context_stays_in_the_history_and_can_come_back(tmp_path: Path):
    store, session = chat_with_two_rounds(tmp_path)
    store.set_rounds_in_context(session.id, "o", [5], False)
    out = store.get_session(session.id, "o")
    assert len(out.history) == 6 and [entry.get("excluded") for entry in out.history[4:]] == [True, True]
    sent = texts(store.build_follow_up_messages(session.id, "o", "Third?"))
    assert "Second?" not in sent and "Second answer." not in sent and "First answer." in sent
    assert sent[-1] == "Third?"
    store.set_rounds_in_context(session.id, "o", [4], True)
    assert "Second answer." in texts(store.get_session(session.id, "o").messages)


def test_without_the_first_answer_the_video_and_the_next_question_become_one_message(tmp_path: Path):
    store, session = chat_with_two_rounds(tmp_path)
    store.set_rounds_in_context(session.id, "o", [3], False)
    messages = store.get_session(session.id, "o").messages
    # Roles still alternate, which chat templates such as Gemma's require.
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert [part["type"] for part in messages[0]["content"]] == ["text", "video_url", "text"]
    assert messages[0]["content"][-1]["text"] == "Second?"


def test_deleting_a_round_removes_its_entries_and_images(tmp_path: Path):
    store, session = chat_with_two_rounds(tmp_path)
    image = store.sessions_dir / session.id / "frame_a.jpg"
    store.delete_rounds(session.id, "o", [1])
    kept = store.get_session(session.id, "o")
    assert [entry["content"] for entry in kept.history] == ["Second?", "Second answer."]
    assert not image.exists()
    assert "First answer." not in texts(kept.messages)


def test_several_rounds_are_hidden_and_deleted_together(tmp_path: Path):
    store, session = chat_with_two_rounds(tmp_path)
    store.set_rounds_in_context(session.id, "o", [2, 5, 99], False)
    assert all(entry.get("excluded") for entry in store.get_session(session.id, "o").history)
    store.delete_rounds(session.id, "o", [0, 4])
    assert store.get_session(session.id, "o").history == []


def test_a_transcript_added_later_goes_at_the_end_and_its_speech_block_with_it(tmp_path: Path):
    store, session = chat_with_two_rounds(tmp_path)
    store.update_chat_settings(session.id, "o", "", False)
    before = store.get_session(session.id, "o")
    store.update_transcript(session.id, "o", "failed", "Skipped: the sound stays private.")
    skipped = store.get_session(session.id, "o")
    # A skipped transcript keeps its reason in the same field; it is not sent as a transcript.
    assert "Skipped" not in json.dumps(skipped.messages)
    store.append_transcript(session.id, "o", "[00:01] Hello")
    added = store.get_session(session.id, "o")
    assert added.transcript_state == "appended" and added.history[-1]["transcript"] is True
    # Everything before it stays the same, so it stays cached.
    assert added.messages[:-1] == before.messages
    assert added.messages[-1]["role"] == "user" and added.messages[-1]["content"].endswith("[00:01] Hello")
    with_question = store.build_follow_up_messages(session.id, "o", "What is said?")
    assert [message["role"] for message in with_question][-2:] == ["assistant", "user"]
    assert texts(with_question)[-1] == "What is said?"
    store.append_transcript(session.id, "o", "")
    assert store.get_session(session.id, "o").transcript_state == "done"


def test_the_speech_blocks_go_only_to_a_model_that_takes_in_the_speech(tmp_path: Path):
    from dataclasses import replace
    from sceneaccess.prompts import SOUND_PROMPT, SPEECH_PROMPT, system_prompt
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="o", source_path=video, title="T", model="", initial_question="?",
                                   system_prompt=system_prompt(""))
    def prompt(session): return session.messages[0]["content"]
    assert SPEECH_PROMPT not in prompt(session) and SOUND_PROMPT not in prompt(session)
    assert prompt(replace(session, hears_sound=True)).endswith(f"{SPEECH_PROMPT}\n{SOUND_PROMPT}")
    store.update_transcript(session.id, "o", "done", "[00:01] Hello")
    transcribed = store.get_session(session.id, "o")
    assert SPEECH_PROMPT in prompt(transcribed) and SOUND_PROMPT not in prompt(transcribed)


def test_moving_a_chat_to_another_provider_stores_it_and_retries_a_skipped_transcript(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="local", source_path=video, title="T", model="", initial_question="?",
                                   provider="llama.cpp")
    store.update_transcript(session.id, "local", "failed", "Skipped: the sound stays private.")
    tools = [{"type": "function", "function": {"name": "zoom_frame"}}]
    store.update_chat_settings(session.id, "local", "", False, provider="openrouter", tools=tools)
    moved = store.get_session(session.id, "local")
    assert (moved.provider, moved.tools, moved.transcript_state, moved.transcript) == ("openrouter", tools, "pending", "")
    store.update_transcript(session.id, "local", "done", "[00:01] Hello")
    store.update_chat_settings(session.id, "local", "", False, provider="llama.cpp", transcribe="on")
    back = store.get_session(session.id, "local")
    # A transcript that exists stays; the tools stay as they were when none are given.
    assert (back.provider, back.transcribe, back.transcript, back.tools) == ("llama.cpp", "on", "[00:01] Hello", tools)


def test_the_transcript_mode_decides_which_models_get_the_transcript(tmp_path: Path):
    from dataclasses import replace
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-mp4-payload")
    store = VideoSessionStore(tmp_path / "app-data")
    session = store.create_session(owner_id="o", source_path=video, title="T", model="", initial_question="?")
    store.update_transcript(session.id, "o", "done", "[00:01] Hello")

    def sent(mode, hears):
        store.update_chat_settings(session.id, "o", "", False, transcribe=mode)
        stored = store.get_session(session.id, "o")
        return "[00:01] Hello" in json.dumps(replace(stored, hears_sound=hears).messages)

    assert sent("auto", hears=False) and not sent("auto", hears=True)
    assert sent("on", hears=False) and sent("on", hears=True)
    assert not sent("off", hears=False) and not sent("off", hears=True)
    # Off only leaves the transcript out: it stays stored, so switching back never pays for it again.
    back = store.get_session(session.id, "o")
    assert (back.transcript_state, back.transcript) == ("done", "[00:01] Hello")
    assert sent("auto", hears=False)
