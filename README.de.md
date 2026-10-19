# SceneAccess

Deutsche Übersetzung der englischen [README](README.md). Die englische README ist die maßgebliche öffentliche Projektdokumentation.

SceneAccess ist ein natives, zugängliches Werkzeug für Gespräche über Videos, gemacht für blinde und sehbehinderte Menschen. Es schickt ein lokales oder ein YouTube-Video über OpenRouter an ein Vision-Modell, speichert den Videokontext lokal, führt Folgefragen mit stabiler OpenRouter-Session-ID weiter und lässt das Modell mit Tools genauer hinsehen.

## Bedienung

1. **Settings** öffnen und Modell, Base-URL sowie optional den API-Key eintragen.
2. **New video chat** wählen, dann **Start chat from local file**, um eine lokale `.mp4`, `.webm` oder `.mov` auszuwählen, oder **Start chat from YouTube**, um einen Link einzugeben. Ein kopierter Link wird automatisch vorgeschlagen.
3. Die erste Frage eingeben und ein **Prompt-Profil** wählen. **Enter** startet den Chat, **Umschalt+Enter** fügt einen Zeilenumbruch ein.
4. Folgefragen im Eingabefeld stellen. **Enter** sendet die Frage, **Umschalt+Enter** fügt einen Zeilenumbruch ein.

Die Liste **Chat history** enthält einen Eintrag pro Nachricht. **Enter** auf einem Eintrag öffnet ihn in einem schreibgeschützten Textfeld zum zeilenweisen Lesen. Unter jeder Antwort zeigt eine Nutzungszeile das Modell, das tatsächlich geantwortet hat (hilfreich bei `openrouter/auto`), die Prompt-Tokens, die aus dem Cache gelesen (hit), nicht aus dem Cache bedient (miss) und in den Cache geschrieben wurden, und bei Tool-Nutzung die Zahl der Anfragen.

Wenn ein laufender Screenreader wie NVDA erkannt wird, sagt SceneAccess an, dass eine Antwort erzeugt wird, sagt jedes Tool an, das das Modell auf diesem Computer ausführt, und setzt den Fokus auf die neue Antwort, sobald sie im aktiven Chatfenster eintrifft.

Die Seitenleiste enthält die letzten Chats. Ein Chat funktioniert weiter, wenn die Originaldatei verschoben oder gelöscht wurde, denn der private lokale Video-Kontext bleibt erhalten.

## Chatfenster und Anfragesteuerung

Jeder Chat öffnet ein eigenes Hauptfenster. **Alt+Tab** wechselt zwischen den Chats; ein bereits offener Chat wird nicht doppelt geöffnet. Die Einstellungen gelten gemeinsam für alle Fenster. Laufende Anfragen behalten den Einstellungsstand vom Start. Eine Antwort im Hintergrund nimmt dem aktuellen Fenster nicht den Fokus weg.

Pro Benutzer-Anwendungsdatenordner läuft nur ein SceneAccess-Prozess. Ein weiterer Start stellt das zuletzt aktive Chatfenster wieder her und aktiviert es, statt einen zweiten Prozess zu öffnen, der Chatverläufe überschreiben könnte. Scheitert die Aktivierung, meldet der neue Prozess, dass SceneAccess bereits läuft, und beendet sich ohne zweite Instanz.

Schließen eines Chats bricht seine Anfrage ab. Bis der alte Worker das Speichern beziehungsweise Beenden abgeschlossen hat, bleibt der Chat gegen eine weitere Anfrage gesperrt. Wiederöffnen ist erlaubt; eine neue Frage bleibt im Eingabefeld und kann kurz darauf erneut gesendet werden. Der Verlauf wird in einer SQLite-Schreibtransaktion ergänzt, sodass abgeschlossene Runden einander nicht überschreiben.

Während einer Anfrage wird **Send question** zu **Cancel request**. Abbrechen unterbricht die aktive API-Verbindung beziehungsweise den lokalen ffmpeg-Prozess für Standbilder oder Audio und verhindert weitere Modell-/Tool-Aufrufe. Bereits übermittelte Arbeit kann der Anbieter weiterhin berechnen. Netzwerkanfragen, HD-Downloads und Metadatenprüfungen laufen in isolierten Arbeitsprozessen, deren Unterprozesse beim Abbruch ebenfalls beendet werden. Andere Vorverarbeitung, etwa das erstmalige Laden des Modellkatalogs, kann erst nach ihrer Rückkehr auf den Abbruch reagieren. Eine fehlgeschlagene oder abgebrochene Frage wird im Eingabefeld wiederhergestellt, solange dort noch keine neue Frage steht. Schließen eines Chatfensters bricht seine laufende Anfrage ab.

**Pause request** hält nach der aktuellen Runde an: Modellantwort und sämtliche davon angeforderten lokalen Tools werden noch fertig ausgeführt; vor der nächsten Modellanfrage pausiert der Ablauf. **Resume request** setzt denselben Ablauf im Arbeitsspeicher fort, ohne erledigte Tools zu wiederholen. Hat das Modell schon seine endgültige Antwort geliefert, wird die Anfrage abgeschlossen statt pausiert. Schließen des Fensters verwirft eine pausierte, unfertige Runde; der abgeschlossene Chatverlauf bleibt gespeichert.

**Settings → Maximum tool rounds per answer** akzeptiert jede nichtnegative ganze Zahl, voreingestellt sind 6. Eine Runde kann mehrere lokale Tools enthalten. Nach dem Limit folgt eine letzte Anfrage mit deaktivierter Tool-Auswahl. Null lässt sämtliche Tools einschließlich der integrierten Websuche aus der Anfrage weg. Positive Limits zählen lokale Tool-Runden, nicht die intern bei OpenRouter ausgeführten Suchen.

Im Startdialog deaktiviert **Disable all tools for this chat** lokale Tools und die integrierte Websuche für diesen Chat. Die Wahl wird mit dem Chat gespeichert und nicht auf neue Chats übertragen. Automatische Audiotranskription ist eine Vorverarbeitung, kein vom Modell gewähltes Tool, und bleibt verfügbar. **Change prompt profile and tools** ersetzt die Tool-Einrichtung ausdrücklich und kann Tools wieder einschalten.

## Prompts

Jeder Chat beginnt mit einem festen Grund-Prompt: für einen blinden Menschen beschreiben, Zeiten als mm:ss angeben, Vermutungen als Vermutungen kennzeichnen, nie Marken oder Text erfinden, Tools nutzen, wenn ein Detail zu klein ist, und in der Sprache des Nutzers antworten. Ein beim Chatstart gewähltes **Prompt-Profil** ergänzt Anweisungen für eine Aufgabe. Voreingestellt sind General description, Audio description, Identify brand and model und Read on-screen text. **Settings → Prompt profiles…** zeigt den Grund-Prompt und erlaubt, Profile zu bearbeiten, hinzuzufügen und zu löschen.

Prompt, Profil und verfügbare Tools werden mit jedem Chat gespeichert und folgen späteren Änderungen in den Settings nicht: Sie gehen in jeder Anfrage dem Video voraus, eine Änderung lässt den Anbieter das ganze Video neu cachen. **Change prompt profile and tools** ändert sie für den geöffneten Chat bewusst, zum Beispiel um Chats aus der Zeit vor den Tools den aktuellen Prompt und die Tools zu geben. Ältere YouTube-Chats haben ihren Link nicht gespeichert: SceneAccess bietet dann die Video-ID aus dem Dateinamen an, falls sie darin steht, und sucht auf YouTube nach dem Titel des Chats. So lässt sich das Video auswählen, ein Link eingeben oder angeben, dass es kein YouTube-Video ist. Mit dem Link bekommt der Chat auch die Tools für hohe Auflösung und Videoinformationen.

## Tools

Das Modell entscheidet selbst, wann es ein Tool nutzt. Jeder Aufruf und jedes Ergebnis der Tools, die auf diesem Computer laufen, erscheint schon während der Arbeit in der Chat history, zum Beispiel `Tool call: Zoom into 02:51, region x 0.4, y 0.5, width 0.2, height 0.1`.

- **Zoom into a frame**: ein Standbild eines Moments, auf Wunsch nur ein Ausschnitt, vergrößert. Aus der Originaldatei oder, falls sie verschoben wurde, aus der gespeicherten Kopie.
- **Load in high resolution** (YouTube-Chats): lädt eine Sekunde an dieser Stelle in bis zu 2160p und zoomt hinein. Nützlich, weil das Chat-Video in niedrigerer Auflösung geladen wird.
- **Read the video information** (YouTube-Chats): Titel, Kanal, Upload-Datum, Beschreibung, Tags und Kapitel, beim Download gespeichert.
- **Websuche**: läuft bei OpenRouter innerhalb derselben Anfrage, wird deshalb während der Suche weder angezeigt noch angesagt; die Quellen stehen unter der Antwort. OpenRouter berechnet jede Suche zusätzlich zu den Tokens.

Bilder werden nach dem Tool-Ergebnis als Nutzernachricht gesendet, weil viele Anbieter Bilder nur vom Nutzer annehmen. Modelle ohne Tool-Unterstützung antworten ohne Tools; die Statuszeile sagt das dann.

Den Cache hält jeder Anbieter selbst, nicht OpenRouter. OpenRouter kann eine Anfrage an einen anderen Anbieter desselben Modells schicken, auch bei fest eingestelltem Modell, und dann wird nichts aus dem Cache gelesen; selbst beim gleichen Anbieter ist der Cache nur nach Möglichkeit. Die Nutzungszeile nennt deshalb den Anbieter, zum Beispiel `model z-ai/glm-5.3-flash via CoreWeave`.

## Ton und Transkript

Anbieterausschlüsse gelten auch für die automatische Spracherkennung. Ist ElevenLabs ausgeschlossen, wird kein Ton zur Transkription gesendet. Der Chat antwortet ohne Transkript und zeigt den Grund ausdrücklich im Verlauf an. Nach erneuter Freigabe des Anbieters kann **Change prompt profile and tools** die Transkription wieder versuchen.

Nicht jedes Modell hört den Ton eines Videos: Gemini-Modelle tun es, viele andere wie GLM sehen nur das Bild. SceneAccess erkennt das an der Modellliste von OpenRouter. Kann das Modell des Chats nicht hören, oder bei `openrouter/auto`, wo das antwortende Modell vorher nicht feststeht, schickt die erste Frage eines Chats zuerst den Ton über die Transkriptions-Schnittstelle von OpenRouter an ElevenLabs Scribe v2 (etwa 0,11 Dollar pro Stunde Ton). Das Transkript mit mm:ss-Zeiten geht in jeder Anfrage mit dem Video mit und wird mit ihm gecacht. Die Chat history zeigt einen kurzen Eintrag Transcript; **Enter** zeigt den vollen Text. Automatische Spracherkennung kann Fehler enthalten, das wird dem Modell auch gesagt. Scheitert die Transkription, antwortet das Modell ohne, und **Change prompt profile and tools** versucht es erneut.

**Settings → Sound: With openrouter/auto, use only models that hear the video's sound** (standardmäßig aus) beschränkt `openrouter/auto` auf Modelle, die Video mit Ton annehmen und Tools unterstützen. Neue Chats brauchen dann kein Transkript.

## Anbieter und Modelle

**Settings → Providers and models** öffnet einen Dialog mit zwei Listen. In der ersten angekreuzte Anbieter (**Leertaste** schaltet um) antworten nie, egal welches Modell; die Liste kommt von OpenRouter. Die zweite Liste enthält Modelle, die `openrouter/auto` nie wählt: **Add model…** findet eines über einen Teil seiner ID, und ein Muster mit `*` wie `google/*` schließt eine ganze Familie aus. Ein unter **Settings → Model** gewähltes Modell wird immer verwendet, ausgeschlossene Anbieter antworten aber auch dafür nicht.

Anbieter begrenzen, wie groß eine Anfrage sein darf, und SceneAccess schickt in jeder Anfrage das ganze Video mit. Getestet im Oktober 2026 über OpenRouter: Google AI Studio lehnt Anfragen über 20.000.000 Bytes ab, also ein Video, das Base64-kodiert größer als etwa 19 MB ist (eine Datei von etwa 14 MB), oft nur mit „HTTP 400: Invalid content“. Google Vertex lehnte 29,8 MB Base64 und mehr ab; zwischen 0,1 MB und diesem Wert wurde nichts getestet. Qwen erlaubt 20 MiB Base64, Xiaomi MiMo nahm 30 MB Base64 an, und Metas Muse Wonder nimmt Dateien bis 50 MiB (vor Base64).

## Videogröße

Bevor ein Chat mit einem Video startet, das gesendet größer als 19 MB wäre, erklärt SceneAccess die Größen und fragt: **Shrink the video**, **Request a lower resolution** (nur YouTube), **Send as it is** oder **Back to Start a video chat**. Verkleinern erzeugt mit ffmpeg eine Kopie, die in 16 MB passt; so bleibt Platz für die Standbilder, die das Modell anfordert, denn sie bleiben im Chat und werden mit jeder späteren Frage erneut gesendet. Bei wenig Platz behält die Kopie lieber 5 Bilder pro Sekunde und ein schärferes Bild als flüssige Bewegung, weil Gemini standardmäßig ein Bild pro Sekunde abtastet, und der Ton bleibt Stereo. Nur die Kopie wird gesendet und mit dem Chat gespeichert; die Originaldatei bleibt unverändert, und solange sie vorhanden ist, kommen Standbilder und Transkript weiter aus ihr. Eine geringere Auflösung lädt das Video eine Qualitätsstufe niedriger neu und stuft weiter ab, bis es passt. Jeder GUI-Download erhält einen eindeutigen Jobpräfix im selben Zielordner, damit keine ältere Datei mit höherer Qualität unbemerkt wiederverwendet wird. Bisherige vollständige Downloads bleiben erhalten, auch wenn der Ersatz scheitert oder abgebrochen wird. Abgebrochene Downloads bereinigen ausschließlich ausdrücklich registrierte Jobdateien mit passender Dateiidentität; fremde Dateien werden weder über Ordnervergleiche noch über Namenspräfixe ausgewählt.

Bei einem anderen Modell als `openrouter/auto` überspringt **Don't show this dialog again** die Frage für dieses Modell, sobald ein großes Video beantwortet wurde. **Settings → Video size** schaltet die Frage ganz ab (zu große Videos scheitern dann) oder fragt für übersprungene Modelle wieder. **Advanced…** im Dialog Start a video chat stellt die YouTube-Qualität ein und kann zu große Videos ohne Nachfrage automatisch auf eine gewählte Größe verkleinern.

Chats, die vor dieser Prüfung begonnen wurden, können ein größeres Video gespeichert haben. Vor einer Frage in so einem Chat bietet SceneAccess an: **Shrink the stored video** (die nächste Frage sendet es dann einmal ungecacht und kostet mehr), **Chat settings…** öffnen, etwa damit `openrouter/auto` ein Modell wählen darf, das nur das Bild sieht und ein Transkript bekommt, oder **Send as it is**. Wurde eine so gesendete Frage beantwortet, fragt der Chat nicht mehr, bis sich sein Video oder seine Chat-Einstellungen ändern.

## Chat-Einstellungen

**Chat settings** legt das Modell des offenen Chats fest. Ein neuer Chat nutzt das Modell aus den Settings und folgt ihm, wenn es dort geändert wird (kann das neue Modell nicht hören, macht die nächste Frage des Chats zuerst ein Transkript, etwa 0,11 Dollar pro Stunde Ton, und sendet das Video einmal ungecacht); ohne Haken bei **Use the model from Settings** bekommt der Chat ein eigenes Modell, eingetippt oder über **Find model…** gefunden. Das chat-eigene Kontrollkästchen **With openrouter/auto, use only models that hear the video's sound** startet mit dem Wert aus den Settings. **Shrink the stored video** erzeugt eine kleinere Kopie für diesen Chat. Ein neues Modell oder ein verkleinertes Video lässt die nächste Frage das Video einmal ungecacht senden; ein Chat ohne Transkript prüft erneut, ob sein neues Modell eines braucht.

## YouTube

Bekannte Einschränkung: Abgebrochene oder fehlgeschlagene native HLS-Downloads können Medienfragmente wie `.part-Frag1.part` im gewählten Downloadordner zurücklassen. SceneAccess weist nach Abbruch oder Fehler darauf hin. Entfernt werden nur ausdrücklich registrierte Dateien mit geprüfter Zugehörigkeit; es gibt keine pauschale Löschung passender Namen oder fremder Downloads. Die Fragmente können private Videodaten enthalten und Speicherplatz belegen. Der native HLS-Downloader bleibt bewusst erhalten.

YouTube-Videos werden mit [yt-dlp](https://github.com/yt-dlp/yt-dlp) in einem Fortschrittsdialog heruntergeladen, der sich abbrechen lässt. Standardmäßig lädt SceneAccess die beste Qualität bis 480p – Details bleiben erkennbar, ohne HD-Dateigrößen – und fügt Bild und Ton zu einer MP4 zusammen, bevorzugt in H.264. In den **Settings** lässt sich zwischen 240p und 1080p wählen. Videos werden unter `scene-access` im Videos-Ordner gespeichert; gibt es diesen Ordner nicht, fragt SceneAccess nach einem Standard-Downloadordner, der sich auch in den **Settings** ändern lässt. Dateien heißen wie beim yt-dlp-Standard `Titel [ID].mp4`; unter **Settings → File name…** lässt sich per Kontrollkästchen jedes yt-dlp-Vorlagenfeld hinzunehmen, weitere Teile stehen in eckigen Klammern. Ein mitgeliefertes ffmpeg fügt Bild und Ton zusammen und schneidet Standbilder aus; ist Deno oder Node.js installiert, nutzt yt-dlp es für vollen YouTube-Support.

Verlangt YouTube „confirm you're not a bot“, fragt SceneAccess, wo du bei YouTube angemeldet bist, und versucht es mit den YouTube-Cookies dieses Browsers erneut; angemeldet bekommt yt-dlp außerdem mehr Videoinformationen. Firefox funktioniert direkt. Chrome und Edge verschlüsseln ihre Cookies so, dass yt-dlp sie unter Windows nicht lesen kann; für sie eine `cookies.txt` mit einer Browser-Erweiterung exportieren und auswählen. **Settings → YouTube sign-in** ändert die Wahl.

## Konfiguration und Datenschutz

SceneAccess verwendet `wx.StandardPaths.GetUserDataDir()` und damit den nativen Benutzer-Anwendungsdatenordner des jeweiligen Betriebssystems. Es gibt keine fest codierten Linux-, Windows- oder Hermes-Pfade.

Die API-Base-URL muss ein vollständiger HTTP(S)-Chat-Completions-Endpunkt sein, etwa `https://openrouter.ai/api/v1/chat/completions`; ein abschließender Schrägstrich ist erlaubt. Modell-, Anbieter- und Audiotranskriptions-Endpunkte werden ausschließlich vom selben API-Ursprung und Pfadpräfix abgeleitet. Uneindeutige Adressen, Zugangsdaten in der URL, Query-Parameter und Fragmente werden abgelehnt. Es gibt keinen automatischen Rückfall auf einen anderen API-Anbieter; ein eigener Endpunkt muss die angeforderte API unterstützen.

`config.json` muss ein JSON-Objekt enthalten. Ungültiges JSON oder ein anderer Wurzeltyp beendet den Start mit einer Fehlermeldung einschließlich Dateipfad. Die Datei bleibt unverändert; SceneAccess setzt Einstellungen nicht stillschweigend zurück und überschreibt sie nicht.

Dort liegen:

- `config.json`: Einstellungen, Prompt-Profile und optional der API-Key
- `chats/sceneaccess.db`: Titel, Modelle, Pfade, Prompts, Tools, Videoinformationen, Transkripte und Gesprächshistorie
- `chats/sessions/<id>/context.data-url`: vollständige rekonstruierbare Videokopie als Base64-Data-URL
- `chats/sessions/<id>/frame_*.jpg`: die Bilder, die das Modell angefordert hat
- `error.log`: unerwartete Fehler, die zusätzlich als Meldung erscheinen
- `instance.port` und plattformspezifische Sperrdateien: lokale Ein-Instanz-Koordination; keine API-Schlüssel oder Chatinhalte

Der API-Key kann alternativ über `OPENROUTER_API_KEY` gesetzt werden, diese Umgebungsvariable hat Vorrang. Base64 ist keine Verschlüsselung. Wer auf `context.data-url` zugreifen kann, besitzt eine Videokopie.

## Code-Struktur

Der gesamte Code liegt im Paket `sceneaccess`:

- `gui.py`: Hauptfenster und App-Start
- `single_instance.py`: Prozesssperre und lokaler Aktivierungskanal mit festem Nachrichtentyp
- `new_chat.py`: einen Chat aus einer lokalen Datei oder von YouTube starten: Chatstart-Dialog, Frage zur Videogröße, Verkleinern und Download
- `dialogs.py`: Settings mit den Unterdialogen File name, Prompt profiles und Providers and models
- `chat_dialogs.py`: der Chatstart-Dialog mit Advanced…, die Fragen zur Videogröße, Chat settings und der Nachrichtenleser
- `chat_history.py`: die Einträge der Chat history und die Nutzungszeile
- `openrouter.py`: Anfragen an OpenRouter und die Schleife der Tool-Aufrufe
- `tools.py`: die Tools für das Modell und der Code, der sie ausführt (ffmpeg-Standbilder, Ausschnitte in hoher Auflösung)
- `prompts.py`: der Grund-Prompt und die Standardprofile
- `models.py`: die Modell- und Anbieterliste von OpenRouter: welche Modelle Ton hören, und die Routing-Felder, die Anbieter und Modelle ausschließen
- `transcript.py`: das Transkript der Sprache eines Videos mit Zeitstempeln, und ob ein Chat eines braucht
- `video_size.py`: wie groß ein Video gesendet ist, und das Verkleinern mit ffmpeg
- `session_store.py`: Chats in SQLite und die privaten Videokopien
- `youtube.py`: yt-dlp-Downloads, Dateinamen und Videoinformationen
- `config.py`: Einstellungen in `config.json`
- `speech.py`: Ansagen über den laufenden Screenreader

`SceneAccess.py` ist der Starter, den PyInstaller verwendet. Die Tests liegen in `tests/`, eine Datei pro Modul (`prompts.py` wird in den Tests von Hauptfenster und Dialogen mitgeprüft), dazu Prüfungen der Doku-Sprache, des Standardmodells und der Release-Dateien.

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
