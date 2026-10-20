import json
import threading

from sceneaccess import session_store
from sceneaccess.session_store import VideoSessionStore


def test_parallel_turn_appends_are_atomic(tmp_path, monkeypatch):
    source = tmp_path/'source.mp4'
    source.write_bytes(b'fixture')
    store = VideoSessionStore(tmp_path/'store')
    session = store.create_session(owner_id='owner', source_path=source, title='Chat', model='test', initial_question='?')
    # The old turn pauses inside record_turn, after reading the history and before writing it back.
    # A second turn appends meanwhile; an append that is not one write transaction would then
    # overwrite it with the stale history it read.
    paused, release = threading.Event(), threading.Event()

    class PausingJson:
        dumps = staticmethod(json.dumps)

        @staticmethod
        def loads(text, *args, **kwargs):
            value = json.loads(text, *args, **kwargs)
            if threading.current_thread().name == 'old-turn':
                paused.set()
                release.wait(5)
            return value

    monkeypatch.setattr(session_store, 'json', PausingJson)
    errors = []

    def append(value):
        try:
            store.record_turn(session.id, 'owner', [{'role': 'assistant', 'content': value}])
        except Exception as error:
            errors.append(error)

    old = threading.Thread(target=append, args=('old',), name='old-turn')
    old.start()
    timer = threading.Timer(0.3, release.set)
    try:
        assert paused.wait(3), 'the old turn never reached the point between reading and writing'
        timer.start()
        append('new')
    finally:
        release.set()
        timer.cancel()
        old.join(5)
    assert not old.is_alive() and not errors
    monkeypatch.undo()
    assert sorted(item['content'] for item in store.get_session(session.id, 'owner').history) == ['new', 'old']
