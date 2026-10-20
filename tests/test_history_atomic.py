import threading

from sceneaccess.session_store import VideoSessionStore


def test_parallel_turn_appends_are_atomic(tmp_path, monkeypatch):
    source = tmp_path/'source.mp4'
    source.write_bytes(b'fixture')
    store = VideoSessionStore(tmp_path/'store')
    session = store.create_session(owner_id='owner', source_path=source, title='Chat', model='test', initial_question='?')
    # The legacy reader pauses after taking its snapshot; a transactional append must
    # read with its own write transaction instead of using this detached snapshot.
    first_read, release = threading.Event(), threading.Event()
    original = store.get_session
    def stale_read(*args):
        result = original(*args)
        if threading.current_thread().name == 'old-turn':
            first_read.set()
            release.wait(2)
        return result
    monkeypatch.setattr(store, 'get_session', stale_read)
    errors = []
    def append(value):
        try:
            store.record_turn(session.id, 'owner', [{'role':'assistant', 'content':value}])
        except Exception as error: errors.append(error)
    old = threading.Thread(target=append, args=('old',), name='old-turn')
    old.start()
    try:
        first_read.wait(.2)
        append('new')
    finally:
        release.set()
        old.join(3)
    assert not old.is_alive() and not errors
    assert sorted(item['content'] for item in original(session.id,'owner').history) == ['new','old']
