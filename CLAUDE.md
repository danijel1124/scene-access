# SceneAccess – Projektregeln

## Gefundene Fehler immer beheben, auch außerhalb der aktuellen Änderung (Nutzer-Vorgabe 30.10.2026)

Fällt bei der Arbeit ein Fehler auf – ein Bug, ein fehlschlagender Test, eine pyflakes-Warnung, toter
Code, eine veraltete Doku-Stelle –, wird er behoben, **auch wenn er nichts mit der gerade laufenden
Änderung zu tun hat**. Nicht nur melden und liegen lassen. Im Abschlussbericht getrennt nennen, was zur
eigentlichen Aufgabe gehört und was nebenbei mitbehoben wurde, jeweils mit Grund.

Gilt mit denselben Sicherungen wie jede andere Änderung: vorher prüfen, dass das Verhalten erhalten
bleibt (oder der Fehler wirklich einer ist), danach die komplette Testsuite laufen lassen.

## Tests nie auf dem sichtbaren Desktop laufen lassen (Nutzer-Vorgabe 30.10.2026)

Viele Tests erzeugen echte wxPython-Fenster (`wx.App`, `MainFrame`, Dialoge). Auf dem normalen Desktop
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

## Tests immer im Hintergrund (Nutzer-Vorgabe 30.10.2026)

Testläufe (die volle Suite wie einzelne Dateien) immer als Hintergrundauftrag starten
(`run_in_background`), nie im Vordergrund auf das Ende warten – damit währenddessen weiter
gesprochen werden kann. Ausgabe in eine Logdatei schreiben und nach der Fertig-Meldung lesen.
