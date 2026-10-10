# SceneAccess

SceneAccess ist ein natives, zugängliches Werkzeug für fortlaufende Gespräche über lokale Videos. Es speichert den Videokontext lokal, führt Folgefragen mit stabiler OpenRouter-Session-ID weiter und ist als Grundlage für spätere Audio-Descriptions gedacht.

## Bedienung

1. **Einstellungen** öffnen und Modell, Base-URL sowie optional den API-Key eintragen.
2. **Neuer Chat mit Video** wählen und eine lokale `.mp4`, `.webm` oder `.mov` auswählen.
3. Die erste Frage eingeben.
4. Folgefragen im Eingabefeld stellen.

Die Seitenleiste enthält die letzten Chats. Ein Chat funktioniert weiter, wenn die Originaldatei verschoben oder gelöscht wurde, denn der private lokale Video-Kontext bleibt erhalten.

## Konfiguration und Datenschutz

SceneAccess verwendet `wx.StandardPaths.GetUserDataDir()` und damit den nativen Benutzer-Anwendungsdatenordner des jeweiligen Betriebssystems. Es gibt keine fest codierten Linux-, Windows- oder Hermes-Pfade.

Dort liegen:

- `config.json`: Modell, Base-URL und optional der API-Key
- `chats/sceneaccess.db`: Titel, Modell, Pfad und Gesprächshistorie
- `chats/sessions/<id>/context.data-url`: vollständige rekonstruierbare Videokopie als Base64-Data-URL

Der API-Key kann alternativ über `OPENROUTER_API_KEY` gesetzt werden, diese Umgebungsvariable hat Vorrang. Base64 ist keine Verschlüsselung. Wer auf `context.data-url` zugreifen kann, besitzt eine Videokopie.

## Start aus dem Quellcode

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -p no:cacheprovider tests/ -q
python SceneAccess.py
```

## Windows-Paket via GitHub Actions

Der Workflow `.github/workflows/windows-release.yml` läuft auf einem echten Windows-Runner. Er testet den Code, baut `SceneAccess.exe` mit PyInstaller im `--onedir`-Modus und erzeugt ein Artifact `SceneAccess-windows-x64.zip`.

Nach dem Entpacken enthält die ZIP direkt:

```text
SceneAccess.exe
_internal/
sceneaccess/
tests/
README.md
...
```

Es gibt keinen zusätzlichen `windows/`-Ordner und keine selbstentpackende EXE. `_internal` muss neben `SceneAccess.exe` bleiben, weil es die Python- und wxPython-Laufzeit enthält.
