import io
import json

import pytest

from sceneaccess import cli
from sceneaccess.app_window import OWNER_ID
from sceneaccess.config import AppConfig, save_config
from sceneaccess.openrouter import Completion, OpenRouterClient, Turn
from sceneaccess.prompts import system_prompt
from sceneaccess.session_store import VideoSessionStore

LLAMA_URL = 'http://192.0.2.1:8080/v1/chat/completions'
OLD_PROMPT = ("You are SceneAccess, an assistant that describes videos for a blind person.\n\n"
              "- Describe the video.\n\nProfile.")


@pytest.fixture
def data_dir(tmp_path):
    save_config(tmp_path / 'config.json', AppConfig(api_key='openrouter-secret', llama_cpp_url=LLAMA_URL,
                                                    llama_cpp_api_key='llama-secret'))
    store = VideoSessionStore(tmp_path / 'chats')
    video = tmp_path / 'v.mp4'
    video.write_bytes(b'fixture, no media decoding')
    session = store.create_session(owner_id=OWNER_ID, source_path=video, title='Chat', model='', initial_question='Describe it.',
                                   system_prompt=OLD_PROMPT)
    store.record_turn(session.id, OWNER_ID, [{'role': 'assistant', 'content': 'Old answer'}])
    store.set_rounds_in_context(session.id, OWNER_ID, [0], False)
    store.update_transcript(session.id, OWNER_ID, 'done', '[00:01] Hello\n[00:02] Second line')
    return tmp_path


def run(*args):
    out = io.StringIO()
    assert cli.main(['--data-dir', str(args[0]), *args[1:]], out) == 0
    return out.getvalue()


def test_chats_lists_each_chat_with_its_transcript_mode_and_an_outdated_prompt(data_dir):
    text = run(data_dir, 'chats')
    assert 'Chat |' in text and 'transcript auto, done' in text and 'outdated system prompt' in text


def test_show_prints_the_prompt_and_marks_what_the_model_no_longer_gets(data_dir):
    text = run(data_dir, 'show', 'Chat')
    assert OLD_PROMPT in text and 'Describe it.' in text and '[hidden from model]' in text and 'Old answer' in text


def test_an_ambiguous_or_unknown_chat_is_refused(data_dir):
    store = VideoSessionStore(data_dir / 'chats')
    store.create_session(owner_id=OWNER_ID, source_path=data_dir / 'v.mp4', title='Chat', model='', initial_question='?')
    with pytest.raises(SystemExit, match='several chats'):
        run(data_dir, 'show', 'Chat')
    with pytest.raises(SystemExit, match='no chat'):
        run(data_dir, 'show', 'Missing')


def test_compare_asks_each_model_as_the_app_would_and_leaves_the_chat_unchanged(data_dir, monkeypatch):
    class Catalog:
        def __init__(self, url): pass
        def hears_audio(self, model): return model == 'hearing/model'

    calls = []

    def answer(client, session, messages, run_tool, on_event=None, routing=None, **kwargs):
        calls.append({'endpoint': client.endpoint, 'key': client.api_key, 'model': session.model,
                      'messages': json.dumps(messages), 'directory': session.directory})
        return Turn(entries=[], completion=Completion(f'Answer of {session.model}', session.model, 1, None, None))

    monkeypatch.setattr(cli, 'ModelCatalog', Catalog)
    monkeypatch.setattr(cli, 'routing', lambda *a, **kw: {})
    monkeypatch.setattr(OpenRouterClient, 'run_turn', answer)
    store = VideoSessionStore(data_dir / 'chats')
    [before] = store.list_sessions(OWNER_ID)
    text = run(data_dir, 'compare', 'Chat', 'llama.cpp', 'hearing/model', 'seeing/model')
    llama, hearing, seeing = calls
    assert (llama['endpoint'], llama['key'], llama['model']) == (LLAMA_URL, 'llama-secret', 'default')
    assert hearing['key'] == seeing['key'] == 'openrouter-secret'
    # The current prompt, no history, and the transcript only for models that cannot hear (Auto).
    for call in calls:
        assert json.dumps(system_prompt('Profile.'))[1:-1] in call['messages'] and 'Old answer' not in call['messages']
        assert call['directory'] != before.directory
    assert '[00:01] Hello' in llama['messages'] and '[00:01] Hello' in seeing['messages']
    assert '[00:01] Hello' not in hearing['messages']
    assert 'Answer of hearing/model' in text and 'system prompt updated: True' in text
    # The report says which models got the transcript, also when it has several lines.
    assert '===== llama.cpp | hears: False | transcript sent: True' in text
    assert '===== hearing/model | hears: True | transcript sent: False' in text
    assert store.get_session(before.id, OWNER_ID) == before


def test_compare_reports_a_failing_model_and_goes_on(data_dir, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError('HTTP 502: fixture')

    monkeypatch.setattr(cli, 'ModelCatalog', lambda url: type('Catalog', (), {'hears_audio': lambda self, m: False})())
    monkeypatch.setattr(cli, 'routing', lambda *a, **kw: {})
    monkeypatch.setattr(OpenRouterClient, 'run_turn', fail)
    text = run(data_dir, 'compare', 'Chat', 'a/model', 'b/model')
    assert text.count('Failed after') == 2 and 'HTTP 502: fixture' in text
