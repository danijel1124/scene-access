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


class FakeAuto:
    def __init__(self, outputs):
        self.outputs = outputs


def test_announce_uses_the_active_screen_reader(monkeypatch):
    reader = FakeOutput(active=True)
    monkeypatch.setattr(speech, "_loaded", True)
    monkeypatch.setattr(speech, "_auto", FakeAuto([FakeOutput(active=False), reader]))

    assert speech.announce("Generating answer…") is True
    assert reader.spoken == [("Generating answer…", True)]


def test_announce_never_falls_back_to_the_sapi_voice(monkeypatch):
    sapi = SAPI5(active=True)
    monkeypatch.setattr(speech, "_loaded", True)
    monkeypatch.setattr(speech, "_auto", FakeAuto([FakeOutput(active=False), sapi]))

    assert speech.announce("Generating answer…") is False
    assert sapi.spoken == []


def test_announce_is_silent_without_the_library(monkeypatch):
    monkeypatch.setattr(speech, "_loaded", True)
    monkeypatch.setattr(speech, "_auto", None)

    assert speech.announce("Generating answer…") is False
