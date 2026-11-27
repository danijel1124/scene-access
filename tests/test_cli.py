import io
import json

import pytest

from sceneaccess import cli
from sceneaccess.llm.client import ChatClient, Completion, Turn
from sceneaccess.pipeline import answering
from sceneaccess.prompts import system_prompt
from sceneaccess.settings.config import AppConfig, save_config
from sceneaccess.storage.session_store import OWNER_ID, VideoSessionStore

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
    session = store.create_session(owner_id=OWNER_ID, source_path=video, title='Chat', model='',
                                   initial_question='Describe it.', system_prompt=OLD_PROMPT)
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
    store.create_session(owner_id=OWNER_ID, source_path=data_dir / 'v.mp4', title='Chat', model='',
                         initial_question='?')
    with pytest.raises(SystemExit, match='several chats'):
        run(data_dir, 'show', 'Chat')
    with pytest.raises(SystemExit, match='no chat'):
        run(data_dir, 'show', 'Missing')


def test_compare_asks_each_model_as_the_app_would_and_leaves_the_chat_unchanged(data_dir, monkeypatch):
    class Catalog:
        def __init__(self, url): pass
        def hears_audio(self, model): return model == 'hearing/model'
        def model_ids(self): return []

    calls = []

    def answer(client, session, messages, run_tool, on_event=None, routing=None, **kwargs):
        calls.append({'endpoint': client.endpoint, 'key': client.api_key, 'model': session.model,
                      'messages': json.dumps(messages), 'directory': session.directory})
        return Turn(entries=[], completion=Completion(f'Answer of {session.model}', session.model, 1, None, None))

    monkeypatch.setattr(cli, 'ModelCatalog', Catalog)
    monkeypatch.setattr(answering, 'llama_cpp_catalog', Catalog)
    monkeypatch.setattr(answering, 'routing', lambda *a, **kw: {})
    monkeypatch.setattr(ChatClient, 'run_turn', answer)
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


def test_compare_sends_a_hearing_llama_cpp_model_its_sound_and_can_try_another_transcript_mode(data_dir, monkeypatch):
    class Server:
        def __init__(self, url): pass
        def model_ids(self): return ['omni.gguf']
        def hears_audio(self, model): return model == 'omni.gguf'

    sent = []
    monkeypatch.setattr(answering, 'llama_cpp_catalog', Server)
    monkeypatch.setattr(cli, 'ModelCatalog', Server)
    monkeypatch.setattr(answering, 'sound_parts',
                        lambda session, control=None: [{'type': 'input_audio', 'input_audio': {'data': 'x'}}])
    monkeypatch.setattr(ChatClient, 'run_turn', lambda client, session, messages, *a, **kw: sent.append(
        json.dumps(messages)) or Turn(entries=[], completion=Completion('Answer', 'omni', 1, None, None)))
    text = run(data_dir, 'compare', 'Chat', 'llama.cpp')
    assert 'input_audio' in sent[0] and '[00:01] Hello' not in sent[0]
    assert 'hears: True (sound sent as a track) | transcript sent: False' in text
    # On sends the transcript as well, for this comparison only.
    text = run(data_dir, 'compare', 'Chat', 'llama.cpp', '--transcribe', 'on')
    assert 'input_audio' in sent[1] and '[00:01] Hello' in sent[1] and 'transcript mode on' in text
    [chat] = VideoSessionStore(data_dir / 'chats').list_sessions(OWNER_ID)
    assert chat.transcribe == 'auto'


def test_compare_repeats_as_new_chats_and_asks_a_follow_up_with_the_answer_as_history(data_dir, monkeypatch):
    sent = []

    def answer(client, session, messages, *a, **kw):
        sent.append(messages)
        reply = f'Answer {len(sent)}'
        return Turn(entries=[{'role': 'assistant', 'content': reply}], completion=Completion(reply, 'm', 1, None, None))

    monkeypatch.setattr(cli, 'ModelCatalog', lambda url: type('Catalog', (), {'hears_audio': lambda self, m: False})())
    monkeypatch.setattr(answering, 'routing', lambda *a, **kw: {})
    monkeypatch.setattr(ChatClient, 'run_turn', answer)
    text = run(data_dir, 'compare', 'Chat', 'a/model', '--runs', '2', '--follow-up', 'Which hand?')
    # Two new chats, each with its answer and then the follow-up.
    assert text.count('Run 1 of 2') == 1 and text.count('Run 2 of 2') == 1 and text.count('Follow-up: Which hand?') == 2
    assert len(sent) == 4
    first, follow, again = sent[0], sent[1], sent[2]
    # The follow-up carries the chat so far: the first request, its answer and the question.
    assert follow[:len(first)] == first and follow[-2] == {'role': 'assistant', 'content': 'Answer 1'}
    assert follow[-1] == {'role': 'user', 'content': 'Which hand?'}
    # The second run starts anew, knowing nothing of the first.
    assert again == first


def test_compare_reports_a_failing_model_and_goes_on(data_dir, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError('HTTP 502: fixture')

    monkeypatch.setattr(cli, 'ModelCatalog', lambda url: type('Catalog', (), {'hears_audio': lambda self, m: False})())
    monkeypatch.setattr(answering, 'routing', lambda *a, **kw: {})
    monkeypatch.setattr(ChatClient, 'run_turn', fail)
    text = run(data_dir, 'compare', 'Chat', 'a/model', 'b/model')
    assert text.count('Failed after') == 2 and 'HTTP 502: fixture' in text
