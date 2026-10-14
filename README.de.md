# SceneAccess

Deutsche Übersetzung der englischen [README](README.md). Die englische README ist die maßgebliche öffentliche Projektdokumentation.

SceneAccess ist ein natives, zugängliches Werkzeug für Gespräche über Videos, gemacht für blinde und sehbehinderte Menschen. Es schickt ein lokales oder ein YouTube-Video über OpenRouter an ein Vision-Modell, speichert den Videokontext lokal, führt Folgefragen mit stabiler OpenRouter-Session-ID weiter und lässt das Modell mit Tools genauer hinsehen.

## Bedienung

1. **Settings** öffnen und Modell, Base-URL sowie optional den API-Key eintragen.
2. **New video chat** wählen, dann **Start chat from local file**, um eine lokale `.mp4`, `.webm` oder `.mov` auszuwählen, oder **Start chat from YouTube**, um einen Link einzugeben. Ein kopierter Link wird automatisch vorgeschlagen.
3. Die erste Frage eingeben und ein **Prompt-Profil** wählen. **Enter** startet den Chat, **Umschalt+Enter** fügt einen Zeilenumbruch ein.
4. Folgefragen im Eingabefeld stellen. **Enter** sendet die Frage, **Umschalt+Enter** fügt einen Zeilenumbruch ein.

Die Liste **Chat history** enthält einen Eintrag pro Nachricht. **Enter** auf einem Eintrag öffnet ihn in einem schreibgeschützten Textfeld zum zeilenweisen Lesen. Unter jeder Antwort zeigt eine Nutzungszeile das Modell, das tatsächlich geantwortet hat (hilfreich bei `openrouter/auto`), die Prompt-Tokens, die aus dem Cache gelesen (hit), nicht aus dem Cache bedient (miss) und in den Cache geschrieben wurden, und bei Tool-Nutzung die Zahl der Anfragen.

Wenn ein laufender Screenreader wie NVDA erkannt wird, sagt SceneAccess an, dass eine Antwort erzeugt wird, sagt jedes Tool an, das das Modell auf diesem Computer ausführt, und setzt den Fokus auf die neue Antwort, sobald sie eintrifft.

Die Seitenleiste enthält die letzten Chats. Ein Chat funktioniert weiter, wenn die Originaldatei verschoben oder gelöscht wurde, denn der private lokale Video-Kontext bleibt erhalten.

## Prompts

Jeder Chat beginnt mit einem festen Grund-Prompt: für einen blinden Menschen beschreiben, Zeiten als mm:ss angeben, Vermutungen als Vermutungen kennzeichnen, nie Marken oder Text erfinden, Tools nutzen, wenn ein Detail zu klein ist, und in der Sprache des Nutzers antworten. Ein beim Chatstart gewähltes **Prompt-Profil** ergänzt Anweisungen für eine Aufgabe. Voreingestellt sind General description, Audio description, Identify brand and model und Read on-screen text. **Settings → Prompt profiles…** zeigt den Grund-Prompt und erlaubt, Profile zu bearbeiten, hinzuzufügen und zu löschen.

Prompt, Profil und verfügbare Tools werden mit jedem Chat gespeichert und folgen späteren Änderungen in den Settings nicht: Sie gehen in jeder Anfrage dem Video voraus, eine Änderung lässt den Anbieter das ganze Video neu cachen. **Change prompt profile and tools** ändert sie für den geöffneten Chat bewusst, zum Beispiel um Chats aus der Zeit vor den Tools den aktuellen Prompt und die Tools zu geben. Ältere YouTube-Chats haben ihren Link nicht gespeichert und bekommen daher nur Zoom und Websuche.

## Tools

Das Modell entscheidet selbst, wann es ein Tool nutzt. Jeder Aufruf und jedes Ergebnis der Tools, die auf diesem Computer laufen, erscheint schon während der Arbeit in der Chat history, zum Beispiel `Tool call: Zoom into 02:51, region x 0.4, y 0.5, width 0.2, height 0.1`.

- **Zoom into a frame**: ein Standbild eines Moments, auf Wunsch nur ein Ausschnitt, vergrößert. Aus der Originaldatei oder, falls sie verschoben wurde, aus der gespeicherten Kopie.
- **Load in high resolution** (YouTube-Chats): lädt eine Sekunde an dieser Stelle in bis zu 2160p und zoomt hinein. Nützlich, weil das Chat-Video in niedrigerer Auflösung geladen wird.
- **Read the video information** (YouTube-Chats): Titel, Kanal, Upload-Datum, Beschreibung, Tags und Kapitel, beim Download gespeichert.
- **Websuche**: läuft bei OpenRouter innerhalb derselben Anfrage, wird deshalb während der Suche weder angezeigt noch angesagt; die Quellen stehen unter der Antwort. OpenRouter berechnet jede Suche zusätzlich zu den Tokens.

Bilder werden nach dem Tool-Ergebnis als Nutzernachricht gesendet, weil viele Anbieter Bilder nur vom Nutzer annehmen. Modelle ohne Tool-Unterstützung antworten ohne Tools; die Statuszeile sagt das dann.

Den Cache hält jeder Anbieter selbst, nicht OpenRouter. OpenRouter kann eine Anfrage an einen anderen Anbieter desselben Modells schicken, auch bei fest eingestelltem Modell, und dann wird nichts aus dem Cache gelesen; selbst beim gleichen Anbieter ist der Cache nur nach Möglichkeit. Die Nutzungszeile nennt deshalb den Anbieter, zum Beispiel `model z-ai/glm-5.3-flash via CoreWeave`.

## YouTube

YouTube-Videos werden mit [yt-dlp](https://github.com/yt-dlp/yt-dlp) in einem Fortschrittsdialog heruntergeladen, der sich abbrechen lässt. Standardmäßig lädt SceneAccess die beste Qualität bis 480p – Details bleiben erkennbar, ohne HD-Dateigrößen – und fügt Bild und Ton zu einer MP4 zusammen, bevorzugt in H.264. In den **Settings** lässt sich zwischen 240p und 1080p wählen. Videos werden unter `scene-access` im Videos-Ordner gespeichert; gibt es diesen Ordner nicht, fragt SceneAccess nach einem Standard-Downloadordner, der sich auch in den **Settings** ändern lässt. Dateien heißen wie beim yt-dlp-Standard `Titel [ID].mp4`; unter **Settings → File name…** lässt sich per Kontrollkästchen jedes yt-dlp-Vorlagenfeld hinzunehmen, weitere Teile stehen in eckigen Klammern. Ein mitgeliefertes ffmpeg fügt Bild und Ton zusammen und schneidet Standbilder aus; ist Deno oder Node.js installiert, nutzt yt-dlp es für vollen YouTube-Support.

## Konfiguration und Datenschutz

SceneAccess verwendet `wx.StandardPaths.GetUserDataDir()` und damit den nativen Benutzer-Anwendungsdatenordner des jeweiligen Betriebssystems. Es gibt keine fest codierten Linux-, Windows- oder Hermes-Pfade.

Dort liegen:

- `config.json`: Einstellungen, Prompt-Profile und optional der API-Key
- `chats/sceneaccess.db`: Titel, Modelle, Pfade, Prompts, Tools, Videoinformationen und Gesprächshistorie
- `chats/sessions/<id>/context.data-url`: vollständige rekonstruierbare Videokopie als Base64-Data-URL
- `chats/sessions/<id>/frame_*.jpg`: die Bilder, die das Modell angefordert hat
- `error.log`: unerwartete Fehler, die zusätzlich als Meldung erscheinen

Der API-Key kann alternativ über `OPENROUTER_API_KEY` gesetzt werden, diese Umgebungsvariable hat Vorrang. Base64 ist keine Verschlüsselung. Wer auf `context.data-url` zugreifen kann, besitzt eine Videokopie.

## Code-Struktur

Der gesamte Code liegt im Paket `sceneaccess`:

- `gui.py`: Hauptfenster und App-Start
- `dialogs.py`: Settings mit den Unterdialogen File name und Prompt profiles, der Chatstart-Dialog und der Nachrichtenleser
- `chat_history.py`: die Einträge der Chat history und die Nutzungszeile
- `openrouter.py`: Anfragen an OpenRouter und die Schleife der Tool-Aufrufe
- `tools.py`: die Tools für das Modell und der Code, der sie ausführt (ffmpeg-Standbilder, Ausschnitte in hoher Auflösung)
- `prompts.py`: der Grund-Prompt und die Standardprofile
- `session_store.py`: Chats in SQLite und die privaten Videokopien
- `youtube.py`: yt-dlp-Downloads, Dateinamen und Videoinformationen
- `config.py`: Einstellungen in `config.json`
- `speech.py`: Ansagen über den laufenden Screenreader

`SceneAccess.py` ist der Starter, den PyInstaller verwendet. Die Tests liegen in `tests/`, eine Datei pro Modul, dazu Prüfungen der Doku-Sprache, des Standardmodells und der Release-Dateien.

## Start aus dem Quellcode

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -p no:cacheprovider tests/ -q
python SceneAccess.py
```

## Windows-Paket durch GitHub Actions

Der Workflow `.github/workflows/windows-release.yml` läuft auf einem echten Windows-Runner. Er testet den Code, baut `SceneAccess.exe` mit PyInstaller im `--onedir`-Modus und lädt ein Artifact `SceneAccess-windows-x64.zip` hoch. Ein gepushter `v*`-Tag veröffentlicht es als GitHub-Release, ein `nightly-JJJJ-MM-TT`-Tag als Vorabversion.

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
