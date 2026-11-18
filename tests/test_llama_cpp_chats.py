import json
import sqlite3

import pytest
import wx

from sceneaccess import gui, models
from sceneaccess.openrouter import Completion, OpenRouterClient, Turn
from sceneaccess.providers import LLAMA_CPP, OPENROUTER, OPENROUTER_URL
from sceneaccess.session_store import (TRANSCRIBE_AUTO, TRANSCRIBE_OFF, TRANSCRIBE_ON, TRANSCRIPT_DONE,
                                       TRANSCRIPT_NONE, TRANSCRIPT_PENDING, VideoSessionStore)
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
    # The fixture server does not exist: its model list is unknown at once, so its model does not hear.
    monkeypatch.setattr(models, 'urlopen', lambda *a, **kw: (_ for _ in ()).throw(OSError('fixture: no server')))
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


def start(frame, *, web_search=False, transcribe=TRANSCRIBE_OFF):
    frame.current = frame.store.create_session(
        owner_id=gui.OWNER_ID, source_path=frame.video, title='A', model='', initial_question='Describe it.',
        tools=tool_definitions(online_video=False, web_search=False, local_web_search=web_search),
        transcript_state=TRANSCRIPT_PENDING, provider=LLAMA_CPP, transcribe=transcribe)


def answered(calls):
    def answer(client, session, messages, run_tool, on_event=None, routing=None, **kwargs):
        calls.append({'endpoint': client.endpoint, 'key': client.api_key, 'model': session.model,
                      'tools': session.tools, 'routing': routing, 'run_tool': run_tool, 'messages': messages})
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
    monkeypatch.setattr(gui, 'wants_transcript', lambda *a: False)
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


def test_with_transcripts_off_the_sound_is_never_sent(frame, monkeypatch):
    start(frame, transcribe=TRANSCRIBE_OFF)
    transcriptions = []
    monkeypatch.setattr(gui, 'make_transcript', lambda *a: transcriptions.append(True) or 'should not run')
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered([]))
    frame._send('Describe it.', initial=True)
    saved = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    assert transcriptions == [] and saved.transcript_state == TRANSCRIPT_NONE and saved.transcript == ''


def test_auto_transcribes_a_llama_cpp_chat_through_openrouter_not_its_server(frame, monkeypatch):
    # llama.cpp reads only the pictures, so in Auto mode its models never hear.
    start(frame, transcribe=TRANSCRIBE_AUTO)
    clients = []
    monkeypatch.setattr(gui, 'make_transcript', lambda session, client: clients.append(client) or '[00:00] Hello')
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered([]))
    frame._send('Describe it.', initial=True)
    [client] = clients
    assert client.endpoint == OPENROUTER_URL and client.api_key == 'openrouter-secret'
    assert frame.store.get_session(frame.current.id, gui.OWNER_ID).transcript_state == TRANSCRIPT_DONE


SOUND = {'type': 'input_audio', 'input_audio': {'data': 'fixture', 'format': 'mp3'}}


class HearingServer:
    """A llama.cpp server running one model that takes sound."""
    def model_ids(self): return [r'D:\models\omni.gguf']
    def hears_audio(self, model): return model == r'D:\models\omni.gguf'


def test_a_llama_cpp_model_that_hears_gets_the_sound_as_a_track_and_no_transcript(frame, monkeypatch):
    start(frame, transcribe=TRANSCRIBE_AUTO)
    # The blocks about the sound are added to a system prompt; a chat without one gets none.
    frame.store.update_system_prompt(frame.current.id, gui.OWNER_ID, 'Base prompt.')
    monkeypatch.setattr(gui, 'llama_cpp_catalog', lambda url: HearingServer())
    monkeypatch.setattr(gui, 'sound_parts', lambda session, control=None: [SOUND])
    monkeypatch.setattr(gui, 'make_transcript', lambda *a: pytest.fail('a model that hears needs no transcript'))
    calls = []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered(calls))
    frame._send('Describe it.', initial=True)
    frame._send('And then?', initial=False)
    assert frame.errors == [] and len(calls) == 2
    for call in calls:
        user = next(message for message in call['messages'] if isinstance(message.get('content'), list))
        kinds = [part['type'] for part in user['content']]
        assert kinds[kinds.index('video_url') + 1] == 'input_audio'
        # It also gets the prompt's block about other sounds, as a model that hears.
        assert 'You also hear the video' in call['messages'][0]['content']
    assert frame.store.get_session(frame.current.id, gui.OWNER_ID).transcript_state == TRANSCRIPT_NONE


def test_without_its_sound_a_hearing_llama_cpp_model_gets_the_transcript_instead(frame, monkeypatch):
    start(frame, transcribe=TRANSCRIBE_AUTO)
    monkeypatch.setattr(gui, 'llama_cpp_catalog', lambda url: HearingServer())

    def broken(session, control=None):
        raise RuntimeError('fixture: ffmpeg failed')

    monkeypatch.setattr(gui, 'sound_parts', broken)
    monkeypatch.setattr(gui, 'make_transcript', lambda session, client: '[00:00] Hello')
    calls = []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answered(calls))
    frame._send('Describe it.', initial=True)
    [call] = calls
    assert 'input_audio' not in json.dumps(call['messages']) and '[00:00] Hello' in json.dumps(call['messages'])


def test_a_streamed_answer_shows_live_and_stays_in_view_when_the_request_fails(frame, monkeypatch):
    start(frame)
    seen = {}

    def stream(client, session, messages, run_tool, on_event=None, routing=None, on_delta=None, **kwargs):
        on_delta('thinking', 'Let me look ' * 30)
        on_delta('text', 'Half of ')
        on_delta('text', 'the answer')
        position = next(i for i, item in enumerate(frame.history_items) if item.startswith('Model is writing'))
        # The item keeps its text, so the screen reader is not disturbed; what Enter shows grows.
        seen['item'] = frame.history_items[position]
        seen['reader'] = frame.reader_texts[position]
        raise RuntimeError('fixture: connection lost')

    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', stream)
    frame._send('Describe it.', initial=True)
    assert seen['item'] == 'Model is writing… (Enter shows it so far)'
    assert seen['reader'].endswith('Half of the answer')
    unfinished = [item for item in frame.history_items if item.startswith('Unfinished, not saved')]
    assert len(unfinished) == 2 and '(90 words; Enter shows it)' in unfinished[0]
    assert unfinished[1] == 'Unfinished, not saved: model wrote: Half of the answer'
    assert any('connection lost' in error for error in frame.errors)


def section_reply(text, merged='The whole video.'):
    """What a model that keeps the rules answers to a request for a video in sections: OK to a check, a description
    starting at its part's first time, the merged answer to the last request."""
    import re
    if 'You are the reviewer' in text:
        return 'OK'
    if 'give the one answer' in text:
        return merged
    found = re.search(r'describe part (\d+) of \d+, from (\d\d:\d\d)', text)
    return f'{found.group(2)} Part {found.group(1)}.' if found else 'A later answer.'


def test_a_long_video_is_described_in_sections_and_later_questions_get_the_text(frame, monkeypatch):
    from sceneaccess.sections import Section
    from sceneaccess.session_store import SECTION_FILE_PREFIX
    frame.current = frame.store.create_session(
        owner_id=gui.OWNER_ID, source_path=frame.video, title='Long', model='', initial_question='Describe it.',
        system_prompt='Base.', tools=tool_definitions(online_video=False, web_search=False), provider=LLAMA_CPP,
        transcribe=TRANSCRIBE_OFF, section_seconds=30)
    directory = frame.current.directory
    cut = []
    for index in range(2):
        path = directory / f'{SECTION_FILE_PREFIX}30s-{index:03d}.mp4'
        path.write_bytes(b'section')
        cut.append(Section(index, index * 30.0, (index + 1) * 30.0, path))
    monkeypatch.setattr(gui, 'video_duration', lambda session: 60.0)
    monkeypatch.setattr(gui, 'cut_sections', lambda session, duration, control=None: cut)
    requests = []

    def answer(client, session, messages, run_tool, on_event=None, routing=None, **kwargs):
        text = json.dumps(messages)
        requests.append(text)
        reply = section_reply(text)
        return Turn(entries=[{'role': 'assistant', 'content': reply}], completion=Completion(reply, 'm', 1, None, None))

    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answer)
    frame._send('Describe it.', initial=True)
    # Each section is described and checked, then all are put together.
    assert frame.errors == [] and len(requests) == 5
    assert 'describe part 1 of 2' in requests[0] and 'You are the reviewer' in requests[1] and 'Part 1 of the video' in requests[1]
    assert 'describe part 2 of 2' in requests[2] and 'video_url' not in requests[1]
    stored = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    assert [entry['text'] for entry in stored.sections['sections']] == ['00:00 Part 1.', '00:30 Part 2.']
    assert stored.history[-1]['content'] == 'The whole video.'
    assert any(item.startswith('Section 1 (00:00 to 00:30, checked)') for item in frame.history_items)
    assert any(item.startswith('Sections joined without the model, Enter shows it: 00:00 Part 1. 00:30 Part 2.')
               for item in frame.history_items), frame.history_items
    frame._send('And then?', initial=False)
    assert 'video_url' not in requests[-1] and 'The whole video.' in requests[-1]
    # With the first answer hidden, a later question still gets what the sections said.
    frame.store.set_rounds_in_context(frame.current.id, gui.OWNER_ID, [0], False)
    frame._send('Once more?', initial=False)
    assert 'The whole video.' not in requests[-1] and 'Part 1.' in requests[-1] and 'video_url' not in requests[-1]


def test_a_reviewer_model_without_an_openrouter_key_stops_before_any_section_is_sent(frame, monkeypatch):
    from sceneaccess.sections import Section
    from sceneaccess.session_store import SECTION_FILE_PREFIX
    frame.config = gui.replace(frame.config, api_key='', section_reviewer_model='vendor/reviewer')
    frame.current = frame.store.create_session(
        owner_id=gui.OWNER_ID, source_path=frame.video, title='Long', model='', initial_question='Describe it.',
        system_prompt='Base.', tools=tool_definitions(online_video=False, web_search=False), provider=LLAMA_CPP,
        transcribe=TRANSCRIBE_OFF, section_seconds=30)
    path = frame.current.directory / f'{SECTION_FILE_PREFIX}30s-000.mp4'
    path.write_bytes(b'section')
    monkeypatch.setattr(gui, 'video_duration', lambda session: 60.0)
    monkeypatch.setattr(gui, 'cut_sections', lambda session, duration, control=None: [Section(0, 0.0, 60.0, path)])
    requests = []
    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', lambda *a, **kw: requests.append(a))
    frame._send('Describe it.', initial=True)
    # Checking with the wrong model would be worse than not starting: the error says which model needs the key.
    assert requests == [] and any('vendor/reviewer' in error and 'OpenRouter API key' in error
                                  for error in frame.errors)


class ServerModel:
    """A llama.cpp server running one model, which does not hear."""
    def __init__(self, name): self.name = name
    def model_ids(self): return [self.name]
    def hears_audio(self, model): return False


def test_a_stopped_description_continues_on_sending_again_but_not_with_another_model(frame, monkeypatch):
    from sceneaccess.sections import Section
    from sceneaccess.session_store import SECTION_FILE_PREFIX
    frame.current = frame.store.create_session(
        owner_id=gui.OWNER_ID, source_path=frame.video, title='Long', model='', initial_question='Describe it.',
        system_prompt='Base.', tools=tool_definitions(online_video=False, web_search=False), provider=LLAMA_CPP,
        transcribe=TRANSCRIBE_OFF, section_seconds=30)
    cut = []
    for index in range(2):
        path = frame.current.directory / f'{SECTION_FILE_PREFIX}30s-{index:03d}.mp4'
        path.write_bytes(b'section')
        cut.append(Section(index, index * 30.0, (index + 1) * 30.0, path))
    monkeypatch.setattr(gui, 'video_duration', lambda session: 60.0)
    monkeypatch.setattr(gui, 'cut_sections', lambda session, duration, control=None: cut)
    server = {'model': 'gemma-12b.gguf', 'fail': True}
    monkeypatch.setattr(gui, 'llama_cpp_catalog', lambda url: ServerModel(server['model']))
    requests = []

    def answer(client, session, messages, run_tool, on_event=None, routing=None, **kwargs):
        text = json.dumps(messages)
        requests.append(text)
        if server['fail'] and 'describe part 2 of 2' in text:
            raise RuntimeError('fixture: server restarted')
        reply = section_reply(text, merged='Whole.')
        return Turn(entries=[{'role': 'assistant', 'content': reply}], completion=Completion(reply, 'm', 1, None, None))

    def parts_asked(since):
        return [number for text in requests[since:] for number in (1, 2) if f'describe part {number} of 2' in text]

    monkeypatch.setattr(gui.OpenRouterClient, 'run_turn', answer)
    frame._send('Describe it.', initial=True)
    assert parts_asked(0) == [1, 2] and any('server restarted' in error for error in frame.errors)
    # The stored first section shows once, as saved, not again as unfinished.
    assert sum('Section 1 (00:00 to 00:30' in item for item in frame.history_items) == 1
    assert not any(item.startswith('Unfinished') and 'Part 1.' in item for item in frame.history_items)
    # Sending the first question again continues with the second section.
    server['fail'] = False
    asked = len(requests)
    frame._send('Describe it.', initial=True)
    assert parts_asked(asked) == [2]
    assert frame.store.get_session(frame.current.id, gui.OWNER_ID).history[-1]['content'] == 'Whole.'
    # Stopped again, then the server is restarted with another model: it starts over rather than mixing models.
    frame.store.delete_rounds(frame.current.id, gui.OWNER_ID, [0])
    server['fail'] = True
    frame._send('Describe it.', initial=True)
    server.update(fail=False, model='gemma-e4b.gguf')
    asked = len(requests)
    frame._send('Describe it.', initial=True)
    assert parts_asked(asked) == [1, 2]


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
def test_a_llama_cpp_chat_offers_the_web_search_only_with_an_openrouter_key(frame, openrouter_key):
    from sceneaccess.chat_dialogs import StartChatDialog
    from sceneaccess.tools import ZOOM_FRAME
    dialog = StartChatDialog(frame, (), '', llama_cpp=True, openrouter_key=openrouter_key, transcribe=TRANSCRIBE_OFF)
    try:
        assert dialog.chosen_tools() == ((ZOOM_FRAME, WEB_SEARCH) if openrouter_key else (ZOOM_FRAME,))
        # The transcript mode is not a tool; selecting all tools leaves it alone.
        dialog._on_toggle_tools(None)
        dialog._on_toggle_tools(None)
        assert dialog.chosen_transcribe() == TRANSCRIBE_OFF
    finally:
        dialog.Destroy()


@pytest.mark.parametrize('llama_cpp', [False, True])
@pytest.mark.parametrize('mode', [TRANSCRIBE_AUTO, TRANSCRIBE_ON, TRANSCRIBE_OFF])
def test_every_new_chat_offers_the_transcript_mode_preselected_from_settings(frame, llama_cpp, mode):
    from sceneaccess.chat_dialogs import StartChatDialog
    dialog = StartChatDialog(frame, (), '', llama_cpp=llama_cpp, openrouter_key=True, transcribe=mode)
    try:
        assert dialog.transcribe.IsShown() and dialog.chosen_transcribe() == mode
    finally:
        dialog.Destroy()


def test_a_new_chat_gets_the_transcript_mode_chosen_when_starting(frame):
    frame.chat_transcribe = TRANSCRIBE_ON
    frame.config = gui.replace(frame.config, transcribe=TRANSCRIBE_OFF)
    frame._send = lambda *a, **kw: None
    frame._start_chat(frame.video, 'C', 'Describe it.', '')
    assert frame.store.get_session(frame.current.id, gui.OWNER_ID).transcribe == TRANSCRIBE_ON


def test_chat_settings_move_a_chat_with_its_tools_to_the_other_provider(frame, monkeypatch):
    from sceneaccess.tools import ZOOM_FRAME, tool_key
    start(frame, web_search=True)

    class Dialog:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ShowModal(self): return wx.ID_OK
        def value(self): return '', False, False
        def provider_value(self): return OPENROUTER, TRANSCRIBE_ON

    monkeypatch.setattr(gui, 'ChatSettingsDialog', Dialog)
    frame._chat_settings_loaded(frame.current.id, {OPENROUTER: [], LLAMA_CPP: []})
    moved = frame.store.get_session(frame.current.id, gui.OWNER_ID)
    assert moved.provider == OPENROUTER and moved.transcribe == TRANSCRIBE_ON
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
        def provider_value(self): return LLAMA_CPP, TRANSCRIBE_AUTO

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
    monkeypatch.setattr(models, 'urlopen', unreachable)
    monkeypatch.setattr(frame.catalog, 'model_ids', lambda: ['openrouter/model'])
    monkeypatch.setattr(frame, '_chat_settings_loaded', lambda session_id, models: loaded.append(models))
    frame.change_chat_settings()
    assert timeouts == [5]
    assert loaded == [{OPENROUTER: ['openrouter/model'], LLAMA_CPP: []}]


def test_only_openrouter_gets_its_own_tools():
    tools = tool_definitions(online_video=False)
    assert provider_tools(tools, OPENROUTER) == tools
    assert provider_tools(tools, LLAMA_CPP) == [tool for tool in tools if tool.get('type') == 'function']


def test_chats_from_before_llama_cpp_support_stay_with_openrouter_and_transcribe_as_needed(tmp_path):
    root = tmp_path / 'chats'
    root.mkdir()
    with sqlite3.connect(root / 'sceneaccess.db') as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, owner_id TEXT, title TEXT, source_path TEXT, "
                     "model TEXT, initial_question TEXT, history_json TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('vid_old', 'o', 'Old', 'x.mp4', '', '?', '[]')")
    old = VideoSessionStore(root).get_session('vid_old', 'o')
    assert old.provider == OPENROUTER and old.transcribe == TRANSCRIBE_AUTO


def test_chats_from_before_the_transcript_mode_all_get_auto(tmp_path):
    # The former per-chat permission for llama.cpp chats is replaced by the mode and no longer read.
    root = tmp_path / 'chats'
    VideoSessionStore(root)
    with sqlite3.connect(root / 'sceneaccess.db') as conn:
        conn.execute("ALTER TABLE sessions RENAME COLUMN transcribe TO dropped")
        conn.execute("ALTER TABLE sessions ADD COLUMN openrouter_transcript TEXT NOT NULL DEFAULT '0'")
        conn.execute("INSERT INTO sessions (id, owner_id, title, source_path, model, initial_question, history_json, "
                     "provider, openrouter_transcript) VALUES ('vid_a', 'o', 'A', 'x.mp4', '', '?', '[]', 'llama.cpp', '0'), "
                     "('vid_b', 'o', 'B', 'x.mp4', '', '?', '[]', 'openrouter', '0')")
    store = VideoSessionStore(root)
    assert [store.get_session(name, 'o').transcribe for name in ('vid_a', 'vid_b')] == [TRANSCRIBE_AUTO] * 2


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
