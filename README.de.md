# SceneAccess

Deutsche Übersetzung der englischen [README](README.md). Die englische README ist die maßgebliche öffentliche Projektdokumentation.

SceneAccess ist ein natives, zugängliches Werkzeug für fortlaufende Gespräche über lokale Videos. Es speichert den Videokontext lokal, führt Folgefragen mit stabiler OpenRouter-Session-ID weiter und ist als Grundlage für spätere Audio-Descriptions gedacht.

## Bedienung

1. **Einstellungen** öffnen und Modell, Base-URL sowie optional den API-Key eintragen.
2. **New video chat** wählen, dann **Start chat from local file**, um eine lokale `.mp4`, `.webm` oder `.mov` auszuwählen, oder **Start chat from YouTube**, um einen Link einzugeben. Ein kopierter Link wird automatisch vorgeschlagen.
3. Die erste Frage eingeben.
4. Folgefragen im Eingabefeld stellen. **Enter** sendet die Frage, **Umschalt+Enter** fügt einen Zeilenumbruch ein.

Die Liste **Chat history** enthält einen Eintrag pro Nachricht. Unter jeder Antwort zeigt eine Nutzungszeile das Modell, das tatsächlich geantwortet hat (hilfreich bei `openrouter/auto`), sowie die Prompt-Tokens, die aus dem Cache gelesen (hit), nicht aus dem Cache bedient (miss) und in den Cache geschrieben wurden. **Enter** auf einem Listeneintrag öffnet ihn in einem schreibgeschützten Textfeld zum zeilenweisen Lesen.

Wenn ein laufender Screenreader wie NVDA erkannt wird, sagt SceneAccess an, dass eine Antwort erzeugt wird, und setzt den Fokus auf die neue Antwort, sobald sie eintrifft.

YouTube-Videos werden mit [yt-dlp](https://github.com/yt-dlp/yt-dlp) in einem Fortschrittsdialog heruntergeladen, der sich abbrechen lässt. Standardmäßig lädt SceneAccess die beste Qualität bis 480p – Details bleiben erkennbar, ohne HD-Dateigrößen – und fügt Bild und Ton zu einer MP4 zusammen. In den **Settings** lässt sich zwischen 240p und 1080p wählen. Videos werden unter `scene-access` im Videos-Ordner gespeichert; gibt es diesen Ordner nicht, fragt SceneAccess nach einem Standard-Downloadordner, der sich auch in den **Settings** ändern lässt. Dateien heißen wie beim yt-dlp-Standard `Titel [ID].mp4`; unter **Settings → File name…** lässt sich per Kontrollkästchen jedes yt-dlp-Vorlagenfeld hinzunehmen, weitere Teile stehen in eckigen Klammern. Ein mitgeliefertes ffmpeg fügt Bild und Ton zusammen; ist Deno oder Node.js installiert, nutzt yt-dlp es für vollen YouTube-Support.

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

## Windows-Paket durch GitHub Actions

Der Workflow `.github/workflows/windows-release.yml` läuft auf einem echten Windows-Runner. Er testet den Code, baut `SceneAccess.exe` mit PyInstaller im `--onedir`-Modus und lädt ein Artifact `SceneAccess-windows-x64.zip` hoch.

Nach dem Entpacken enthält die ZIP direkt:

```text
SceneAccess.exe
_internal/
sceneaccess/
tests/
README.md
README.de.md
...
```

Es gibt keinen zusätzlichen `windows/`-Ordner und keine selbstentpackende EXE. `_internal` muss neben `SceneAccess.exe` bleiben, weil es die Python- und wxPython-Laufzeit enthält.
