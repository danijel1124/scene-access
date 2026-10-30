import json
import sqlite3

import pytest
import wx

from sceneaccess import gui
from sceneaccess.openrouter import Completion, OpenRouterClient, Turn
from sceneaccess.providers import LLAMA_CPP, OPENROUTER, OPENROUTER_URL
from sceneaccess.session_store import TRANSCRIPT_DONE, TRANSCRIPT_FAILED, TRANSCRIPT_PENDING, VideoSessionStore
from sceneaccess.tools import WEB_SEARCH, ToolResult, ToolRunner, function_tool_names, provider_tools, tool_definitions

LLAMA_URL = 'http://192.0.2.1:8080/v1/chat/completions'


class InlineThread:
    def __init__(self, target, **kw): self.target = target
    def start(self): self.target()


@pytest.fixture
def frame(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setattr(gui, 'config_path_from_standard_paths', lambda _: tmp_path / 'config.json')
    monkeypatch.setattr(gui, 'announce', lambda *a, **kw: None)
    monkeypatch.setattr(gui.wx, 'CallAfter', lambda f, *a, **kw: f(*a, **kw))
    monkeypatch.setattr(gui.threading, 'Thread', InlineThread)
    app = wx.App(False)
    window = gui.ChatFrame()
    window.config = gui.replace(window.config, api_key='openrouter-secret', provider=LLAMA_CPP,
                                llama_cpp_url=LLAMA_URL, llama_cpp_api_key='llama-secret')
    window.errors = []
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, *a, **kw: window.errors.append(message))
    window.video = tmp_path / 'fixture.mp4'
    window.video.write_bytes(b'fixture, no media decoding')
    yield window
    window.Destroy()
    app.Destroy()


def start(frame, *, web_search=False, openrouter_transcript=False):
    frame.current = frame.store.create_session(
        owner_id=gui.OWNER_ID, source_path=frame.video, title='A', model='', initial_question='Describe it.',
        tools=tool_definitions(online_video=False, web_search=False, local_web_search=web_search),
        transcript_state=TRANSCRIPT_PENDING, provider=LLAMA_CPP, openrouter_transcript=openrouter_transcript)


def answered(calls):
    def answer(client, session, messages, run_tool, on_event=None, routing=None, **kwargs):
        calls.append({'endpoint': client.endpoint, 'key': client.api_key, 'model': session.model,
                      'tools': session.tools, 'routing': routing, 'run_tool': run_tool})
        return Turn(entries=[{'role': 'assistant', 'content': 'Answer'}],
                    completion=Completion('Answer', 'fixture', 1, None, None))
    return answer


def test_a_llama_cpp_chat_goes_only_to_its_server_with_its_own_key(frame, monkeypatch):
    start(frame)
    calls = []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered(calls))
    frame._send('Describe it.', initial=True)
    assert frame.errors == []
    [call] = calls
    assert call['endpoint'] == LLAMA_URL and call['key'] == 'llama-secret'
    # No OpenRouter routing, model or tools reach the user's own server.
    assert call['routing'] == {} and call['model'] == 'default'
    assert not any(str(tool.get('type')).startswith('openrouter:') for tool in call['tools'])


def test_a_llama_cpp_chat_stays_on_its_server_after_settings_switch_to_openrouter(frame, monkeypatch):
    start(frame)
    frame.config = gui.replace(frame.config, provider=OPENROUTER)
    calls = []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered(calls))
    frame._send('Describe it.', initial=True)
    [call] = calls
    assert call['endpoint'] == LLAMA_URL and call['key'] == 'llama-secret'


def test_an_openrouter_chat_stays_with_openrouter_after_settings_switch_to_llama_cpp(frame, monkeypatch):
    frame.config = gui.replace(frame.config, ignored_providers=('fixture-provider',))
    frame.current = frame.store.create_session(
        owner_id=gui.OWNER_ID, source_path=frame.video, title='B', model='', initial_question='Describe it.',
        tools=tool_definitions(online_video=False))
    monkeypatch.setattr(gui, 'needs_transcript', lambda *a: False)
    calls = []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered(calls))
    frame._send('Describe it.', initial=True)
    [call] = calls
    assert call['endpoint'] == OPENROUTER_URL and call['key'] == 'openrouter-secret'
    assert call['model'] == frame.config.model and call['routing'] == {'provider': {'ignore': ['fixture-provider']}}
    assert any(str(tool.get('type')).startswith('openrouter:') for tool in call['tools'])


def test_a_llama_cpp_chat_needs_no_openrouter_key(frame, monkeypatch):
    frame.config = gui.replace(frame.config, api_key='')
    start(frame)
    calls = []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered(calls))
    frame._send('Describe it.', initial=True)
    assert frame.errors == [] and len(calls) == 1


def test_a_llama_cpp_chat_without_its_server_url_keeps_the_question(frame, monkeypatch):
    start(frame)
    frame.config = gui.replace(frame.config, llama_cpp_url='')
    calls = []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered(calls))
    frame._send('Keep this question', initial=True)
    assert calls == [] and frame.question.GetValue() == 'Keep this question'
    assert 'llama.cpp' in frame.errors[-1]


def test_the_sound_stays_private_unless_the_chat_allowed_openrouter(frame, monkeypatch):
    start(frame)
    transcriptions = []
    monkeypatch.setattr(gui, 'make_transcript', lambda *a: transcriptions.append(True) or 'should not run')
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered([]))
    frame._send('Describe it.', initial=True)
    saved = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    assert transcriptions == []
    assert saved.transcript_state == TRANSCRIPT_FAILED and 'llama.cpp' in saved.transcript


def test_an_allowed_transcript_is_made_by_openrouter_not_the_llama_cpp_server(frame, monkeypatch):
    start(frame, openrouter_transcript=True)
    clients = []
    monkeypatch.setattr(gui, 'make_transcript', lambda session, client: clients.append(client) or '[00:00] Hello')
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered([]))
    frame._send('Describe it.', initial=True)
    [client] = clients
    assert client.endpoint == OPENROUTER_URL and client.api_key == 'openrouter-secret'
    assert frame.store.get_session(frame.current.id, gui.OWNER_ID).transcript_state == TRANSCRIPT_DONE


def test_the_web_search_tool_searches_through_openrouter_with_the_settings_model(frame, monkeypatch):
    frame.config = gui.replace(frame.config, ignored_providers=('fixture-provider',))
    start(frame, web_search=True)
    calls, searches = [], []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered(calls))
    def search(client, query, model, routing=None):
        searches.append((client.endpoint, client.api_key, query, model, routing))
        return 'Found it.', [{'title': 'Example', 'url': 'https://example.com/'}]
    monkeypatch.setattr(gui.OpenRouterClient, 'web_search', search)
    frame._send('Describe it.', initial=True)
    assert WEB_SEARCH in function_tool_names(calls[0]['tools'])
    result = calls[0]['run_tool'](WEB_SEARCH, '{"query": "red logo"}')
    # Excluded providers never answer, the search included.
    assert searches == [(OPENROUTER_URL, 'openrouter-secret', 'red logo', frame.config.model,
                         {'provider': {'ignore': ['fixture-provider']}})]
    assert 'Found it.' in result.text and 'https://example.com/' in result.text


def test_the_web_search_request_carries_only_the_query_and_the_exclusions():
    sent = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({'choices': [{'message': {'content': 'Found.', 'annotations': [
            {'type': 'url_citation', 'url_citation': {'url': 'https://example.com/', 'title': 'Example'}}]}}]}).encode()
    def opener(request, timeout):
        sent.append(json.loads(request.data))
        return Response()
    text, sources = OpenRouterClient('secret', opener=opener).web_search(
        'red logo', 'fixture/model', {'provider': {'ignore': ['fixture-provider']}})
    [payload] = sent
    assert payload['model'] == 'fixture/model' and payload['provider'] == {'ignore': ['fixture-provider']}
    assert 'red logo' in payload['messages'][0]['content'] and 'video' not in json.dumps(payload).lower()
    assert text == 'Found.' and sources == [{'title': 'Example', 'url': 'https://example.com/'}]


def test_the_web_search_tool_explains_a_missing_openrouter_key(tmp_path):
    video = tmp_path / 'v.mp4'
    video.write_bytes(b'fixture')
    session = VideoSessionStore(tmp_path / 'chats').create_session(
        owner_id='o', source_path=video, title='T', model='', initial_question='?',
        tools=tool_definitions(online_video=False, web_search=False, local_web_search=True), provider=LLAMA_CPP)
    assert 'OpenRouter API key' in ToolRunner(session).run(WEB_SEARCH, '{"query": "x"}').text
    found = ToolRunner(session, web_search=lambda query: ToolResult(f'about {query}')).run(WEB_SEARCH, '{"query": "x"}')
    assert found.text == 'about x'


def test_an_error_names_the_chats_provider_and_explains_a_too_small_context(frame, monkeypatch):
    start(frame)
    shown = []
    monkeypatch.setattr(gui.wx, 'MessageBox', lambda message, title='', *a, **kw: shown.append((title, message)))
    frame._failed('HTTP 400: request (21969 tokens) exceeds the available context size (16384 tokens)')
    title, message = shown[-1]
    assert title == 'Own llama.cpp server error' and 'larger -c' in message


@pytest.mark.parametrize('openrouter_key', [False, True])
def test_a_llama_cpp_chat_offers_search_and_transcript_only_with_an_openrouter_key(frame, openrouter_key):
    from sceneaccess.chat_dialogs import StartChatDialog
    from sceneaccess.tools import ZOOM_FRAME
    dialog = StartChatDialog(frame, (), '', llama_cpp=True, openrouter_key=openrouter_key)
    try:
        assert dialog.transcript.IsShown() and dialog.transcript.IsEnabled() is openrouter_key
        # The transcript is not a tool and off by default; selecting all tools leaves it alone.
        assert not dialog.transcript.GetValue()
        assert dialog.chosen_tools() == ((ZOOM_FRAME, WEB_SEARCH) if openrouter_key else (ZOOM_FRAME,))
        dialog._on_toggle_tools(None)
        dialog._on_toggle_tools(None)
        assert not dialog.transcript.GetValue()
    finally:
        dialog.Destroy()


def test_an_openrouter_chat_has_no_transcript_choice(frame):
    from sceneaccess.chat_dialogs import StartChatDialog
    dialog = StartChatDialog(frame, (), '', openrouter_key=True)
    try:
        assert not dialog.transcript.IsShown()
    finally:
        dialog.Destroy()


def test_chat_settings_move_a_chat_with_its_tools_to_the_other_provider(frame, monkeypatch):
    from sceneaccess.tools import ZOOM_FRAME, tool_key
    start(frame, web_search=True)

    class Dialog:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ShowModal(self): return wx.ID_OK
        def value(self): return '', False, False
        def provider_value(self): return OPENROUTER, False

    monkeypatch.setattr(gui, 'ChatSettingsDialog', Dialog)
    frame._chat_settings_loaded(frame.current.id, {OPENROUTER: [], LLAMA_CPP: []})
    moved = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    assert moved.provider == OPENROUTER
    # Same tools, now with OpenRouter's own web search instead of the one through OpenRouter.
    assert [tool_key(tool) for tool in moved.tools] == [ZOOM_FRAME, WEB_SEARCH]
    assert any(tool.get('type') == 'openrouter:web_search' for tool in moved.tools)
    assert 'via OpenRouter' in frame.status.GetLabel()


def test_moving_to_llama_cpp_without_an_openrouter_key_drops_the_web_search(frame, monkeypatch):
    from sceneaccess.tools import ZOOM_FRAME, tool_key
    frame.config = gui.replace(frame.config, api_key='')
    frame.current = frame.store.create_session(
        owner_id=gui.OWNER_ID, source_path=frame.video, title='B', model='', initial_question='?',
        tools=tool_definitions(online_video=False))

    class Dialog:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ShowModal(self): return wx.ID_OK
        def value(self): return '', False, False
        def provider_value(self): return LLAMA_CPP, False

    monkeypatch.setattr(gui, 'ChatSettingsDialog', Dialog)
    frame._chat_settings_loaded(frame.current.id, {OPENROUTER: [], LLAMA_CPP: []})
    moved = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    assert moved.provider == LLAMA_CPP and [tool_key(tool) for tool in moved.tools] == [ZOOM_FRAME]


def test_chat_settings_wait_only_briefly_for_the_llama_cpp_model_list(frame, monkeypatch):
    start(frame)
    timeouts, loaded = [], []
    def unreachable(request, timeout):
        timeouts.append(timeout)
        raise OSError('fixture: server switched off')
    monkeypatch.setattr(gui, 'urlopen', unreachable)
    monkeypatch.setattr(frame.catalog, 'model_ids', lambda: ['openrouter/model'])
    monkeypatch.setattr(frame, '_chat_settings_loaded', lambda session_id, models: loaded.append(models))
    frame.change_chat_settings()
    assert timeouts == [5]
    assert loaded == [{OPENROUTER: ['openrouter/model'], LLAMA_CPP: []}]


def test_only_openrouter_gets_its_own_tools():
    tools = tool_definitions(online_video=False)
    assert provider_tools(tools, OPENROUTER) == tools
    assert provider_tools(tools, LLAMA_CPP) == [tool for tool in tools if tool.get('type') == 'function']


def test_chats_from_before_llama_cpp_support_stay_with_openrouter(tmp_path):
    root = tmp_path / 'chats'
    root.mkdir()
    with sqlite3.connect(root / 'sceneaccess.db') as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, owner_id TEXT, title TEXT, source_path TEXT, "
                     "model TEXT, initial_question TEXT, history_json TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('vid_old', 'o', 'Old', 'x.mp4', '', '?', '[]')")
    old = VideoSessionStore(root).get_session('vid_old', 'o')
    assert old.provider == OPENROUTER and old.openrouter_transcript is False


def test_an_empty_key_sends_no_authorization_header():
    sent = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({'choices': [{'message': {'content': 'ok'}}]}).encode()
    def opener(request, timeout):
        sent.append(dict(request.header_items()))
        return Response()
    OpenRouterClient('', base_url=LLAMA_URL, opener=opener)._post({})
    OpenRouterClient('secret', opener=opener)._post({})
    assert 'Authorization' not in sent[0] and sent[1]['Authorization'] == 'Bearer secret'
