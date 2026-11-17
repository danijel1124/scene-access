# SceneAccess – Projektregeln

## Gefundene Fehler immer beheben, auch außerhalb der aktuellen Änderung (Nutzer-Vorgabe 30.10.2026)

Fällt bei der Arbeit ein Fehler auf – ein Bug, ein fehlschlagender Test, eine pyflakes-Warnung, toter
Code, eine veraltete Doku-Stelle –, wird er behoben, **auch wenn er nichts mit der gerade laufenden
Änderung zu tun hat**. Nicht nur melden und liegen lassen. Im Abschlussbericht getrennt nennen, was zur
eigentlichen Aufgabe gehört und was nebenbei mitbehoben wurde, jeweils mit Grund.

Gilt mit denselben Sicherungen wie jede andere Änderung: vorher prüfen, dass das Verhalten erhalten
bleibt (oder der Fehler wirklich einer ist), danach die komplette Testsuite laufen lassen.

## Tests nie auf dem sichtbaren Desktop laufen lassen (Nutzer-Vorgabe 30.10.2026)

Viele Tests erzeugen echte wxPython-Fenster (`wx.App`, `ChatFrame`, `ChatListFrame`, Dialoge). Auf dem normalen Desktop
gehen die beim Nutzer auf, stehlen den Fokus und verwirren NVDA – real passiert, der Nutzer: „die Tests
machen eigene Fenster auf, das stört abartig und verwirrt NVDA“. Deshalb legt `tests/conftest.py`
unter Windows vor allen Tests einen eigenen, unsichtbaren Desktop an (`CreateDesktopW` +
`SetThreadDesktop` über ctypes) und bricht ab, statt Fenster sichtbar zu öffnen, falls das scheitert.
pytest darf damit ganz normal gestartet werden. **Diese Absicherung nie entfernen oder umgehen**, und
neue Tests, die Fenster in einem anderen Thread oder Prozess als dem pytest-Hauptthread erzeugen, ebenfalls
auf den unsichtbaren Desktop bringen: Ein neuer Thread oder Prozess landet sonst wieder auf dem Desktop
des Nutzers. **Kindprozesse beachten:** Ein neuer Prozess startet auf dem Desktop des Nutzers, nicht auf dem
unsichtbaren. Tests, die einen Kindprozess mit GUI starten (z. B. `gui.main()` in
`test_single_instance.py`), müssen darin vor dem ersten Fenster `conftest.move_to_invisible_desktop()`
aufrufen. Real passiert (30.10.2026): Ein solcher Kindprozess öffnete sein Fenster sichtbar, und ein
Wächter, der nur die Fenster des pytest-Prozesses selbst prüfte, hat es übersehen. Ein Wächter muss
deshalb die Fenster aller Nachfahren-Prozesse prüfen.

**Auch die Zwischenablage gehört dem Nutzer:** Sie gilt für die ganze Windows-Sitzung, nicht nur für den
unsichtbaren Desktop. Tests ersetzen `wx.TheClipboard` deshalb immer durch eine Attrappe, nie die echte
benutzen – sonst überschreibt jeder Testlauf, was der Nutzer gerade kopiert hat.

## Nach jeder Runde SceneAccess wieder starten (Nutzer-Vorgabe 30.10.2026)

Der Nutzer beendet SceneAccess, bevor eine Änderungsrunde beginnt. Am Ende jeder Runde, sobald alle
Tests grün sind (und committet ist, wenn committet werden soll), SceneAccess ungefragt wieder starten:
als Hintergrundauftrag des Harness (`run_in_background`), damit Ausgaben und Tracebacks lesbar
bleiben, und danach prüfen, dass das Fenster offen ist. Sind die Tests nicht grün, erst beheben, dann
starten. Während Tests laufen, SceneAccess nicht parallel starten (Speicher dieses Rechners).

## Python-Umgebung liegt im Repo unter `.venv` (Nutzer-Vorgabe 17.11.2026)

SceneAccess, Tests und pyflakes laufen mit `.venv\Scripts\python.exe` im Repo-Ordner (per `.gitignore`
ausgeschlossen), angelegt mit `py -3.12 -m venv .venv` und `pip install -r requirements-dev.txt`. Nie
ins Scratchpad von Claude Code legen: Das liegt im Temp-Ordner und wird aufgeräumt. Real passiert
(17.11.2026): Dem venv dort fehlten plötzlich `pyvenv.cfg` und Teile von wx und pip, SceneAccess startete
nicht mehr, und die Hilfsskripte daneben waren ganz weg.

Dauerhaft gebrauchte Hilfsskripte liegen deshalb in `tools/`, nicht im Scratchpad:
- `tools\guarded_pytest.py <Logdatei> <pytest-Argumente>`: startet pytest und bricht sofort ab, wenn
  pytest oder ein Kindprozess ein sichtbares Fenster öffnet. So werden Tests gestartet.
- `tools\e2e_llama.py [URL]`: schickt einen echten Chat an den llama.cpp-Server (ohne URL die aus den
  Settings), mit SceneAccess' eigener Sitzung und Werkzeugschleife.

Chats des Nutzers lesen und Modelle vergleichen geht über die Kommandozeile der App (siehe README, „Start
aus dem Quellcode“): `python -m sceneaccess.cli chats`, `show "<Titel>"` und `compare "<Titel>" <Modelle…>`.
`compare` arbeitet auf einer Kopie des Chats; nur OpenRouter-Modelle kosten dabei Geld.

## Tests immer im Hintergrund (Nutzer-Vorgabe 30.10.2026)

Testläufe (die volle Suite wie einzelne Dateien) immer als Hintergrundauftrag starten
(`run_in_background`), nie im Vordergrund auf das Ende warten – damit währenddessen weiter
gesprochen werden kann. Ausgabe in eine Logdatei schreiben und nach der Fertig-Meldung lesen.
