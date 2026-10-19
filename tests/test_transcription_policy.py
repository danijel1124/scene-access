import pytest
from sceneaccess.openrouter import OpenRouterClient


def test_excluded_transcriber_is_blocked_before_the_http_boundary():
    sent = []
    def opener(*args, **kwargs):
        sent.append(True)
        pytest.fail('Excluded provider must receive no request')
    client = OpenRouterClient('fixture-only',opener=opener,ignored_providers=('elevenlabs',))
    assert not client.transcription_allowed()
    with pytest.raises(ValueError,match='excluded'):
        client.transcribe(b'private audio')
    assert not sent
