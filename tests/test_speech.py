from types import SimpleNamespace

import pytest

from accessible_output2.outputs.e_speak import ESpeak
from accessible_output2.outputs.speech_dispatcher import SpeechDispatcher
from sceneaccess import speech


class FakeOutput:
    def __init__(self, active):
        self.active = active
        self.spoken = []

    def is_active(self):
        return self.active

    def speak(self, text, interrupt=False):
        self.spoken.append((text, interrupt))

    def braille(self, text, **options):
        return None


class SAPI5(FakeOutput):
    pass


class NVDA(FakeOutput):
    pass


class FakeAuto:
    def __init__(self, outputs):
        self.outputs = outputs


@pytest.mark.parametrize('reader_name', ['NVDA', 'Jaws', 'WindowEyes', 'SystemAccess',
                                       'Dolphin', 'PCTalker', 'ZDSR', 'VoiceOver'])
def test_announce_uses_the_active_screen_reader(monkeypatch, reader_name):
    reader_type = type(reader_name, (FakeOutput,), {})
    reader = reader_type(active=True)
    monkeypatch.setattr(speech, '_loaded', True)
    monkeypatch.setattr(speech, '_auto', FakeAuto([NVDA(active=False), reader]))
    assert speech.announce('Generating answer…') is True
    assert reader.spoken == [('Generating answer…', True)]


def test_announce_never_falls_back_to_the_sapi_voice(monkeypatch):
    sapi = SAPI5(active=True)
    monkeypatch.setattr(speech, '_loaded', True)
    monkeypatch.setattr(speech, '_auto', FakeAuto([NVDA(active=False), sapi]))
    assert speech.announce('Generating answer…') is False
    assert sapi.spoken == []


def test_announce_is_silent_without_the_library(monkeypatch):
    monkeypatch.setattr(speech, '_loaded', True)
    monkeypatch.setattr(speech, '_auto', None)
    assert speech.announce('Generating answer…') is False


@pytest.mark.parametrize('backend_type', [ESpeak, SpeechDispatcher])
def test_available_linux_synthesizer_is_not_a_screen_reader(monkeypatch, backend_type):
    spoken = []
    backend = backend_type.__new__(backend_type)
    if backend_type is ESpeak:
        backend._ec = SimpleNamespace(synth=spoken.append, cancel=lambda: None)
    else:
        backend._client = SimpleNamespace(speak=lambda text, **kw: spoken.append(text), cancel=lambda: None)
    assert backend.is_active()
    monkeypatch.setattr(speech, '_loaded', True)
    monkeypatch.setattr(speech, '_auto', FakeAuto([backend]))
    assert speech._screen_reader() is None
    assert speech.announce('Private model answer', interrupt=False) is False
    assert spoken == []


def test_active_screen_reader_after_linux_synthesizer_still_receives_speech(monkeypatch):
    spoken = []
    synthesizer = ESpeak.__new__(ESpeak)
    synthesizer._ec = SimpleNamespace(synth=spoken.append, cancel=lambda: None)
    reader = NVDA(active=True)
    monkeypatch.setattr(speech, '_loaded', True)
    monkeypatch.setattr(speech, '_auto', FakeAuto([synthesizer, reader]))
    assert speech.announce('Model answer') is True
    assert spoken == [] and reader.spoken == [('Model answer', True)]


def test_unknown_output_is_not_implicitly_treated_as_a_screen_reader(monkeypatch):
    unknown = FakeOutput(active=True)
    monkeypatch.setattr(speech, '_loaded', True)
    monkeypatch.setattr(speech, '_auto', FakeAuto([unknown]))
    assert speech.announce('Private model answer') is False
    assert unknown.spoken == []
