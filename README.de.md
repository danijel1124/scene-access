# SceneAccess

Deutsche Übersetzung der englischen [README](README.md). Die englische README ist die maßgebliche öffentliche Projektdokumentation.

SceneAccess ist ein natives, zugängliches Werkzeug für Gespräche über Videos, gemacht für blinde und sehbehinderte Menschen. Es schickt ein lokales oder ein YouTube-Video über OpenRouter oder an den eigenen llama.cpp-Server an ein Vision-Modell, speichert den Videokontext lokal, führt Folgefragen mit stabiler OpenRouter-Session-ID weiter und lässt das Modell mit Tools genauer hinsehen.

## Bedienung

SceneAccess startet mit seinem Hauptfenster, der Liste **Recent video chats**. **Enter** oder ein Doppelklick auf einen Chat öffnet ihn in einem eigenen Chatfenster; Blättern mit den Pfeiltasten öffnet nichts.

1. Im Hauptfenster **Settings** öffnen, den **Anbieter für neue Chats** wählen und OpenRouter-Modell und API-Key oder die Adresse des eigenen llama.cpp-Servers eintragen (siehe [Eigener llama.cpp-Server](#eigener-llamacpp-server)).
2. Im Hauptfenster **New video chat** wählen, dann **Start chat from local file**, um eine lokale `.mp4`, `.webm` oder `.mov` auszuwählen, oder **Start chat from YouTube**, um einen Link einzugeben. Ein kopierter Link wird automatisch vorgeschlagen.
3. Die erste Frage eingeben und ein **Prompt-Profil** wählen. **Enter** startet den Chat in einem neuen Chatfenster, **Umschalt+Enter** fügt einen Zeilenumbruch ein.
4. Folgefragen im Eingabefeld des Chatfensters stellen. **Enter** sendet die Frage, **Umschalt+Enter** fügt einen Zeilenumbruch ein.

Die Liste **Chat history** enthält einen Eintrag pro Nachricht. **Enter** auf einem Eintrag öffnet ihn in einem schreibgeschützten Textfeld zum zeilenweisen Lesen. Unter jeder Antwort zeigt eine Nutzungszeile das Modell, das tatsächlich geantwortet hat (hilfreich bei `openrouter/auto`), die Prompt-Tokens, die aus dem Cache gelesen (hit), nicht aus dem Cache bedient (miss) und in den Cache geschrieben wurden, und bei Tool-Nutzung die Zahl der Anfragen.

Die Antwort wird gestreamt: Während das Modell nachdenkt oder schreibt, zeigt die Chat history einen Eintrag **Model is thinking…** oder **Model is writing…**, und **Enter** darauf zeigt, was bisher da ist. Der Text des Eintrags selbst ändert sich dabei nicht, damit der Screenreader nicht unterbrochen wird. Ist die Antwort gespeichert, bleibt der Gedankengang des Modells, sofern es einen zeigt, als eingeklappter Eintrag **Model's thinking (N words; Enter shows it)** mit nur seinem Anfang stehen; Enter zeigt ihn ganz. Er geht nicht noch einmal an das Modell, und Verstecken oder Löschen der Antwort nimmt ihn mit. Scheitert eine Anfrage oder wird sie abgebrochen, bleibt das bis dahin Gestreamte als Einträge **Unfinished, not saved** in der Liste, bis sie neu aufgebaut wird, etwa bei der nächsten Frage.

In der Chat history lassen sich mehrere Einträge auswählen: **Umschalt+Pfeiltasten** erweitern die Auswahl, **Strg+Pfeiltasten** bewegen sich ohne Auswahl, **Strg+Leertaste** wählt den aktuellen Eintrag aus oder ab. Das Kontextmenü (Kontextmenü-Taste, Umschalt+F10 oder Rechtsklick) wirkt auf die Auswahl. **Copy to clipboard** kopiert den vollen Text der ausgewählten Einträge. Die übrigen Einträge wirken auf die Runden der ausgewählten Antworten: Eine Runde ist ihre Frage, ihre Tool-Schritte samt Bildern und die Antwort. **Hide this answer from the model** (bzw. **these answers**) behält die Runden im Verlauf, markiert mit „[hidden from model]“, schickt sie aber nicht mehr mit; **Show this answer to the model again** macht das rückgängig. **Delete this answer from the history** entfernt die Runden samt Bildern nach Rückfrage. Beides hilft, die Antworten verschiedener Modelle auf dieselbe Frage zu vergleichen, und beides lässt den Anbieter das Gespräch ab dieser Stelle einmal ungecacht lesen. Die erste Frage und das Transkript gehören zum Video und bleiben; sie lassen sich nur kopieren. Solange der Chat antwortet, lässt sich sein Verlauf nicht ändern, aber kopieren.

Wenn ein laufender Screenreader wie NVDA erkannt wird, sagt SceneAccess an, dass eine Antwort erzeugt wird, sagt jedes Tool an, das das Modell auf diesem Computer ausführt, und setzt den Fokus auf die neue Antwort, sobald sie im aktiven Chatfenster eintrifft.

Direkte Ansagen verwenden nur bekannte Screenreader-Anbindungen. Verfügbare reine Synthesizer wie SAPI, ESpeak oder Speech Dispatcher lösen keine Sprachausgabe aus; unbekannte Ausgabemodule bleiben stumm. Eine direkte Orca-Anbindung für Ansagen ist nicht enthalten.

Ein Chat funktioniert weiter, wenn die Originaldatei verschoben oder gelöscht wurde, denn der private lokale Video-Kontext bleibt erhalten.

## Hauptfenster und Chatfenster

Das Hauptfenster enthält **New video chat**, **Settings**, die Liste **Recent video chats** und **Delete chat**; das löscht nach Rückfrage den in der Liste ausgewählten Chat und schließt vorher sein offenes Chatfenster. Ein Chat, der noch antwortet oder speichert, wird nicht gelöscht. Schließen des Hauptfensters beendet SceneAccess und schließt alle Chatfenster; laufende Anfragen werden abgebrochen, abgeschlossene Chats bleiben gespeichert.

Jeder Chat öffnet sich in einem eigenen Chatfenster, **Alt+Tab** wechselt also zwischen den Chats. Ein Chatfenster hat die Statuszeile, die **Chat history**, das Eingabefeld mit **Send question** und **Pause request** sowie die chateigenen Knöpfe **Chat settings**, **Change prompt profile and tools** und **Update original file path**. Ein bereits offener Chat wird nicht doppelt geöffnet, sein Fenster kommt nach vorn. Der Fokus geht auf die Chat history mit der neuesten Antwort; schließt ein Chatfenster, kehrt er zur Liste zurück. Die Einstellungen gelten gemeinsam für alle Fenster, gespeicherte Settings erscheinen sofort in offenen Chatfenstern. Laufende Anfragen behalten den Einstellungsstand vom Start. Eine Antwort im Hintergrund nimmt dem aktuellen Fenster nicht den Fokus weg.

Pro Benutzer-Anwendungsdatenordner läuft nur ein SceneAccess-Prozess. Ein weiterer Start stellt das zuletzt benutzte Fenster wieder her und aktiviert es, statt einen zweiten Prozess zu öffnen, der Chatverläufe überschreiben könnte. Scheitert die Aktivierung, meldet der neue Prozess, dass SceneAccess bereits läuft, und beendet sich ohne zweite Instanz.

Schließen eines Chats bricht seine Anfrage ab. Bis der alte Worker das Speichern beziehungsweise Beenden abgeschlossen hat, bleibt der Chat gegen eine weitere Anfrage gesperrt. Wiederöffnen ist erlaubt; eine neue Frage bleibt im Eingabefeld und kann kurz darauf erneut gesendet werden. Der Verlauf wird in einer SQLite-Schreibtransaktion ergänzt, sodass abgeschlossene Runden einander nicht überschreiben.

Während einer Anfrage wird **Send question** zu **Cancel request**. Abbrechen unterbricht die aktive API-Verbindung beziehungsweise den lokalen ffmpeg-Prozess für Standbilder oder Audio und verhindert weitere Modell-/Tool-Aufrufe. Bereits übermittelte Arbeit kann der Anbieter weiterhin berechnen. Netzwerkanfragen, HD-Downloads und Metadatenprüfungen laufen in isolierten Arbeitsprozessen, deren Unterprozesse beim Abbruch ebenfalls beendet werden. Andere Vorverarbeitung, etwa das erstmalige Laden des Modellkatalogs, kann erst nach ihrer Rückkehr auf den Abbruch reagieren. Eine fehlgeschlagene oder abgebrochene Frage wird im Eingabefeld wiederhergestellt, solange dort noch keine neue Frage steht. Schließen eines Chatfensters bricht seine laufende Anfrage ab.

**Pause request** hält nach der aktuellen Runde an: Modellantwort und sämtliche davon angeforderten lokalen Tools werden noch fertig ausgeführt; vor der nächsten Modellanfrage pausiert der Ablauf. **Resume request** setzt denselben Ablauf im Arbeitsspeicher fort, ohne erledigte Tools zu wiederholen. Hat das Modell schon seine endgültige Antwort geliefert, wird die Anfrage abgeschlossen statt pausiert. Schließen des Fensters verwirft eine pausierte, unfertige Runde; der abgeschlossene Chatverlauf bleibt gespeichert.

**Settings → Maximum tool rounds per answer** akzeptiert jede nichtnegative ganze Zahl, voreingestellt sind 6. Eine Runde kann mehrere lokale Tools enthalten. Nach dem Limit folgt eine letzte Anfrage mit deaktivierter Tool-Auswahl. Null lässt sämtliche Tools einschließlich der integrierten Websuche aus der Anfrage weg. Positive Limits zählen lokale Tool-Runden, nicht die intern bei OpenRouter ausgeführten Suchen.

Im Startdialog hat **Tools for this chat** ein Kontrollkästchen pro Tool, alle standardmäßig an; die Tools für hohe Auflösung und Videoinformationen erscheinen nur bei YouTube-Videos. **Deselect all tools** schaltet alle aus und heißt danach **Select all tools**. Die Wahl wird mit dem Chat gespeichert und nicht auf neue Chats übertragen. Automatische Audiotranskription ist eine Vorverarbeitung, kein vom Modell gewähltes Tool, und bleibt verfügbar. **Change prompt profile and tools** ersetzt die Tool-Einrichtung ausdrücklich und kann Tools wieder einschalten.

## Prompts

Jeder Chat beginnt mit einem festen Grund-Prompt: für einen blinden Menschen beschreiben, bei Frontkamera-Aufnahmen vor „links“ oder „rechts“ prüfen, ob das Bild gespiegelt ist, Zeiten als mm:ss angeben, Vermutungen als Vermutungen kennzeichnen, nie Marken oder Text erfinden, Tools nutzen, wenn ein Detail zu klein ist, und in der Sprache des Nutzers antworten. Ein beim Chatstart gewähltes **Prompt-Profil** ergänzt Anweisungen für eine Aufgabe. Voreingestellt sind General description, Audio description, Identify brand and model und Read on-screen text. **Settings → Prompt profiles…** zeigt den Grund-Prompt und erlaubt, Profile zu bearbeiten, hinzuzufügen und zu löschen.

Ein Chat behält den Systemprompt, mit dem er begonnen hat, damit sein Video im Cache bleibt, auch wenn Grund-Prompt oder Standardprofile später besser werden. Solange ein Chat noch eine frühere Fassung hat, oder gar keinen Systemprompt, weil er älter ist als die Systemprompts, zeigt sein Fenster **Update system prompt**. Der Knopf baut den Prompt mit dem aktuellen Grund-Prompt und dem Profil des Chats neu, mit dem aktuellen Standardtext des Profils, falls es noch einen früheren hatte; ein Chat ohne Systemprompt bekommt den Grund-Prompt. Tools und Transkript des Chats bleiben. Er fragt vorher nach, weil die nächste Frage das Video einmal ungecacht sendet; danach verschwindet er.

Prompt, Profil und verfügbare Tools werden mit jedem Chat gespeichert und folgen späteren Änderungen in den Settings nicht: Sie gehen in jeder Anfrage dem Video voraus, eine Änderung lässt den Anbieter das ganze Video neu cachen. **Change prompt profile and tools** ändert sie für den geöffneten Chat bewusst, zum Beispiel um Chats aus der Zeit vor den Tools den aktuellen Prompt und die Tools zu geben. Ältere YouTube-Chats haben ihren Link nicht gespeichert: SceneAccess bietet dann die Video-ID aus dem Dateinamen an, falls sie darin steht, und sucht auf YouTube nach dem Titel des Chats. So lässt sich das Video auswählen, ein Link eingeben oder angeben, dass es kein YouTube-Video ist. Mit dem Link bekommt der Chat auch die Tools für hohe Auflösung und Videoinformationen.

## Tools

Das Modell entscheidet selbst, wann es ein Tool nutzt. Jeder Aufruf und jedes Ergebnis der Tools, die auf diesem Computer laufen, erscheint schon während der Arbeit in der Chat history, zum Beispiel `Tool call: Zoom into 02:51, region x 0.4, y 0.5, width 0.2, height 0.1`.

- **Zoom into a frame**: ein Standbild eines Moments, auf Wunsch nur ein Ausschnitt, vergrößert. Aus der Originaldatei oder, falls sie verschoben wurde, aus der gespeicherten Kopie.
- **Load in high resolution** (YouTube-Chats): lädt eine Sekunde an dieser Stelle in bis zu 2160p und zoomt hinein. Nützlich, weil das Chat-Video in niedrigerer Auflösung geladen wird.
- **Read the video information** (YouTube-Chats): Titel, Kanal, Upload-Datum, Beschreibung, Tags und Kapitel, beim Download gespeichert.
- **Websuche**: läuft bei OpenRouter innerhalb derselben Anfrage, wird deshalb während der Suche weder angezeigt noch angesagt; die Quellen stehen unter der Antwort. OpenRouter berechnet jede Suche zusätzlich zu den Tokens. Ein llama.cpp-Chat bekommt sie nur, wenn sie beim Chatstart gewählt wurde: Dann ist sie ein Tool auf diesem Computer, das wie die anderen angezeigt und angesagt wird und das OpenRouter-Modell aus den Settings suchen lässt; nur die Suchbegriffe verlassen diesen Computer.

Bilder werden nach dem Tool-Ergebnis als Nutzernachricht gesendet, weil viele Anbieter Bilder nur vom Nutzer annehmen. Modelle ohne Tool-Unterstützung antworten ohne Tools; die Statuszeile sagt das dann.

Den Cache hält jeder Anbieter selbst, nicht OpenRouter. OpenRouter kann eine Anfrage an einen anderen Anbieter desselben Modells schicken, auch bei fest eingestelltem Modell, und dann wird nichts aus dem Cache gelesen; selbst beim gleichen Anbieter ist der Cache nur nach Möglichkeit. Die Nutzungszeile nennt deshalb den Anbieter, zum Beispiel `model z-ai/glm-5.3-flash via CoreWeave`.

## Ton und Transkript

Anbieterausschlüsse gelten auch für die automatische Spracherkennung. Ist ElevenLabs ausgeschlossen, wird kein Ton zur Transkription gesendet. Der Chat antwortet ohne Transkript und zeigt den Grund ausdrücklich im Verlauf an. Nach erneuter Freigabe des Anbieters kann **Change prompt profile and tools** die Transkription wieder versuchen.

Nicht jedes Modell hört den Ton eines Videos: Gemini-Modelle tun es, viele andere wie GLM sehen nur das Bild. SceneAccess erkennt das an der Modellliste von OpenRouter. Im Standard-Transkriptmodus Auto (siehe unten) gilt: Kann das Modell des Chats nicht hören, oder bei `openrouter/auto`, wo das antwortende Modell vorher nicht feststeht, schickt die erste Frage eines Chats zuerst den Ton über die Transkriptions-Schnittstelle von OpenRouter an ElevenLabs Scribe v2 (etwa 0,11 Dollar pro Stunde Ton). Das Transkript mit mm:ss-Zeiten geht in jeder Anfrage mit dem Video mit und wird mit ihm gecacht. Die Chat history zeigt einen kurzen Eintrag Transcript; **Enter** zeigt den vollen Text. Automatische Spracherkennung kann Fehler enthalten, das wird dem Modell auch gesagt. Scheitert die Transkription, antwortet das Modell ohne, und **Change prompt profile and tools** versucht es erneut.

**Settings → Sound: With openrouter/auto, use only models that hear the video's sound** (standardmäßig aus) beschränkt `openrouter/auto` auf Modelle, die Video mit Ton annehmen und Tools unterstützen. Neue Chats brauchen dann kein Transkript.

Jeder Chat hat einen Modus **Transcript of the speech**, für beide Anbieter gleich:

- **Auto** erstellt und schickt ein Transkript nur, solange das Modell des Chats das Video nicht hört; ein hörendes Modell bekommt keines. llama.cpp beachtet den Ton eines Videos nicht. Nimmt das Modell auf dem eigenen Server Ton an, wie Qwen3-Omni, meldet das seine Modellliste, und SceneAccess schickt den Ton des Videos als eigene Spur mit, einmal herausgezogen und beim Chat gespeichert; das Modell hört dann, und nichts geht an OpenRouter. Ein Modell auf dem eigenen Server ohne Toneingabe bekommt in Auto ein Transkript, wie jedes Modell, das nicht hört. SceneAccess fragt den Server vor jeder Frage, damit auch ein mit anderem Modell neu gestarteter Server richtig behandelt wird.
- **On** erstellt und schickt immer ein Transkript, auch an hörende Modelle.
- **Off** schickt den Ton nie zur Transkription. Hörende Modelle hören das Video weiterhin; die anderen erfahren nichts vom Gesagten.

**Settings → Transcript of the speech in new chats** legt fest, mit welchem Modus neue Chats starten (standardmäßig Auto), und **Start a video chat** kann ihn für den neuen Chat ändern. Bestehende Chats behalten ihren eigenen Modus; **Chat settings** ändert ihn ab der nächsten Frage. Ein Transkript entsteht immer über OpenRouter und braucht daher einen OpenRouter-API-Key; dabei geht der Ton des Videos raus, nie das Bild. Ein Transkript, das der Modus weglässt, bleibt gespeichert, sodass das Zurückschalten nie erneut kostet.

Manche Modelle auf dem eigenen Server nehmen nur kurze Videos und kurzen Ton an; Gemma 4 zum Beispiel etwa 60 Bilder und 30 Sekunden Ton. **Settings → llama.cpp: describe longer videos in sections of at most this many seconds** (0, der Standard, sendet jedes Video ganz) lässt neue llama.cpp-Chats ein längeres Video in gleich langen Abschnitten beschreiben, keiner länger als dieser Wert, jeder mit ffmpeg genau geschnitten und beim Chat gespeichert. Jeder Abschnitt bekommt eine eigene Anfrage mit eigenem Bild und Ton, dem Teil des Transkripts, der in ihn fällt, und allem, was das Modell zu den Abschnitten davor geschrieben hat; die Chat history zeigt jeden Abschnitt, sobald er dran ist. Jede Beschreibung wird danach zweimal geprüft: mechanisch auf Zeilen, die nur wiederholen, was gesagt wird, und auf Zeiten, die fehlen, durcheinander, außerhalb des Abschnitts oder nicht mm:ss sind; und vom selben Modell in einer eigenen Anfrage ohne Video und ohne Chat, das auch mit anderen Worten Nacherzähltes findet. Diese Anfrage hat einen eigenen Systemprompt, der dem Modell sagt, dass es der Prüfer ist, was es prüft und welche Regeln eine Beschreibung einhalten muss. Nur wenn eine der Prüfungen etwas findet, geht der Abschnitt mit seinem Video, seiner ersten Beschreibung und den Funden zurück ans Modell und wird einmal neu beschrieben; die Chat history zeigt dann die Prüfung mit ihren Funden und der ersten Beschreibung. Eine letzte Anfrage ohne Video führt die Beschreibungen zur Antwort zusammen und behält jede Einzelheit und Zeit. Außerdem bleiben die Abschnitte ohne Modell aneinandergereiht erhalten, ohne eine Zeile, mit der ein Abschnitt das Ende des vorigen wiederholt, als **Sections joined without the model**. Die Beschreibung jedes Abschnitts wird gespeichert, sobald sie fertig ist, als Einträge **Section N (von bis)**, eingeklappt wie der Gedankengang: Bricht die Beschreibung ab, weil sie abgebrochen wurde, fehlschlug oder der Server neu startete, setzt erneutes Senden der ersten Frage beim nächsten Abschnitt fort. Haben sich dazwischen Frage, Modell, Prompt, Abschnittslänge oder Transkriptmodus geändert, beginnt sie neu. Folgefragen bekommen die Antwort als Text statt des Videos, das für das Modell zu lang ist; die Zoom-Werkzeuge sehen weiter das ganze Video. Die Einstellung zieht später in Einstellungen pro Modell um.

**Add a transcript of the speech** im Chatfenster erstellt nachträglich ein Transkript über OpenRouter und ElevenLabs und hängt es ans Ende des Chats statt vor das Video, sodass gecachtes Video und Verlauf gültig bleiben. Dabei wird das Modell nichts gefragt; die nächste Frage schickt das Transkript mit, unabhängig vom Transkriptmodus des Chats. Das Transkript steht in der Chat history und lässt sich wie eine Antwort vor dem Modell verstecken oder löschen.

Nur ein Modell, das das Gesagte erfasst, weil es das Video hört oder ein Transkript bekommt, erhält die Anweisungen des Grund-Prompts dazu: das Gesagte als Kontext dafür zu nutzen, welche sichtbaren Details wichtig sind, ohne es ungefragt zu zitieren. Nur ein Modell, das hört, erhält zusätzlich die Anweisung, andere Geräusche zu nennen. Ein Modell, das beides nicht bekommt, etwa ein llama.cpp-Modell ohne Toneingabe und ohne Transkript, wird davon nicht verwirrt.

## Anbieter und Modelle

**Settings → Providers and models** öffnet einen Dialog mit zwei Listen. In der ersten angekreuzte Anbieter (**Leertaste** schaltet um) antworten nie, egal welches Modell; die Liste kommt von OpenRouter. Die zweite Liste enthält Modelle, die `openrouter/auto` nie wählt: **Add model…** findet eines über einen Teil seiner ID, und ein Muster mit `*` wie `google/*` schließt eine ganze Familie aus. Ein unter **Settings → Model** gewähltes Modell wird immer verwendet, ausgeschlossene Anbieter antworten aber auch dafür nicht.

Anbieter begrenzen, wie groß eine Anfrage sein darf, und SceneAccess schickt in jeder Anfrage das ganze Video mit. Getestet im Oktober 2026 über OpenRouter: Google AI Studio lehnt Anfragen über 20.000.000 Bytes ab, also ein Video, das Base64-kodiert größer als etwa 19 MB ist (eine Datei von etwa 14 MB), oft nur mit „HTTP 400: Invalid content“. Google Vertex lehnte 29,8 MB Base64 und mehr ab; zwischen 0,1 MB und diesem Wert wurde nichts getestet. Qwen erlaubt 20 MiB Base64, Xiaomi MiMo nahm 30 MB Base64 an, und Metas Muse Wonder nimmt Dateien bis 50 MiB (vor Base64).

## Eigener llama.cpp-Server

**Settings → Provider for new chats** wählt zwischen OpenRouter und dem eigenen [llama.cpp](https://github.com/ggml-org/llama.cpp)-Server. Jeder Chat behält den Anbieter, mit dem er begonnen hat; ein auf dem eigenen Server begonnener Chat schickt sein Video also nie an OpenRouter, auch nicht nach einer Änderung der Settings. Die Statuszeile nennt den Anbieter des Chats.

**Chat settings → Provider of this chat** zieht einen Chat zum anderen Anbieter um. Der Chat folgt dann dem Modell des neuen Anbieters aus den Settings, bis er ein eigenes bekommt, behält seine gewählten Tools mit der Websuche dieses Anbieters und ein vorhandenes Transkript; ein übersprungenes Transkript wird bei der nächsten Frage erneut versucht. Ein Umzug vom eigenen Server zu OpenRouter fragt vorher nach, denn ab der nächsten Frage geht das Video an OpenRouter und, sofern der Transkriptmodus nicht Off ist, der Ton an ElevenLabs, wenn ein Transkript gebraucht wird. Chat settings ändert außerdem den Modus **Transcript of the speech** des Chats. Auf die Modellliste des eigenen Servers wartet Chat settings höchstens etwa fünf Sekunden; ohne sie lässt sich ein Modell weiterhin eintippen.

Für llama.cpp-Chats die Chat-Completions-Adresse des Servers eintragen, etwa `http://192.168.1.20:8080/v1/chat/completions`, optional einen Modellnamen (ein Server mit einem Modell antwortet unabhängig vom Namen) und optional den Schlüssel von `llama-server --api-key`. Nur dieser Schlüssel geht an den Server, nie der OpenRouter-Key. Der Server braucht ein Vision-Modell mit seiner `--mmproj`-Datei, Video-Unterstützung (llama.cpp ab Juni 2026 mit installiertem ffmpeg und ffprobe; unter Windows ein Build nach dem 24. August 2026) und `--jinja` für die Tools. OpenRouters Routing, Anbieterausschlüsse und die integrierte Websuche werden dafür nicht verwendet.

Ein llama.cpp-Chat nutzt OpenRouter nur für zwei Dinge, beide nur mit OpenRouter-API-Key: Das Tool **Web search through OpenRouter**, gewählt in **Start a video chat**, schickt nur Suchbegriffe, und ein Transkript, so wie es der Transkriptmodus verlangt (siehe Ton und Transkript), schickt den Ton des Videos. **Chat settings** listet die Modelle des eigenen Servers.

## Videogröße

Bevor ein Chat mit einem Video startet, das gesendet größer als 19 MB wäre, erklärt SceneAccess die Größen und fragt: **Shrink the video**, **Request a lower resolution** (nur YouTube), **Send as it is** oder **Back to Start a video chat**. Verkleinern erzeugt mit ffmpeg eine Kopie, die in 16 MB passt; so bleibt Platz für die Standbilder, die das Modell anfordert, denn sie bleiben im Chat und werden mit jeder späteren Frage erneut gesendet. Bei wenig Platz behält die Kopie lieber 5 Bilder pro Sekunde und ein schärferes Bild als flüssige Bewegung, weil Gemini standardmäßig ein Bild pro Sekunde abtastet, und der Ton bleibt Stereo. Nur die Kopie wird gesendet und mit dem Chat gespeichert; die Originaldatei bleibt unverändert, und solange sie vorhanden ist, kommen Standbilder und Transkript weiter aus ihr. Eine geringere Auflösung lädt das Video eine Qualitätsstufe niedriger neu und stuft weiter ab, bis es passt. Jeder GUI-Download erhält einen eindeutigen Jobpräfix im selben Zielordner, damit keine ältere Datei mit höherer Qualität unbemerkt wiederverwendet wird. Bisherige vollständige Downloads bleiben erhalten, auch wenn der Ersatz scheitert oder abgebrochen wird. Abgebrochene Downloads bereinigen ausschließlich ausdrücklich registrierte Jobdateien mit passender Dateiidentität; fremde Dateien werden weder über Ordnervergleiche noch über Namenspräfixe ausgewählt.

Bei einem anderen Modell als `openrouter/auto` überspringt **Don't show this dialog again** die Frage für dieses Modell, sobald ein großes Video beantwortet wurde. **Settings → Video size** schaltet die Frage ganz ab (zu große Videos scheitern dann) oder fragt für übersprungene Modelle wieder. **Advanced…** im Dialog Start a video chat stellt die YouTube-Qualität ein und kann zu große Videos ohne Nachfrage automatisch auf eine gewählte Größe verkleinern.

Chats, die vor dieser Prüfung begonnen wurden, können ein größeres Video gespeichert haben. Vor einer Frage in so einem Chat bietet SceneAccess an: **Shrink the stored video** (die nächste Frage sendet es dann einmal ungecacht und kostet mehr), **Chat settings…** öffnen, etwa damit `openrouter/auto` ein Modell wählen darf, das nur das Bild sieht und ein Transkript bekommt, oder **Send as it is**. Wurde eine so gesendete Frage beantwortet, fragt der Chat nicht mehr, bis sich sein Video oder seine Chat-Einstellungen ändern.

## Chat-Einstellungen

**Chat settings** legt das Modell des offenen Chats fest. Ein neuer Chat nutzt das Modell aus den Settings und folgt ihm, wenn es dort geändert wird (verlangt der Transkriptmodus des Chats dann ein Transkript, das er noch nicht hat, macht die nächste Frage zuerst eines, etwa 0,11 Dollar pro Stunde Ton, und sendet das Video einmal ungecacht); ohne Haken bei **Use the model from Settings** bekommt der Chat ein eigenes Modell, eingetippt oder über **Find model…** gefunden. Das chat-eigene Kontrollkästchen **With openrouter/auto, use only models that hear the video's sound** startet mit dem Wert aus den Settings. **Shrink the stored video** erzeugt eine kleinere Kopie für diesen Chat. Ein neues Modell oder ein verkleinertes Video lässt die nächste Frage das Video einmal ungecacht senden; ein Chat ohne Transkript prüft erneut, ob sein neues Modell eines braucht.

## YouTube

Dateinamenvorlagen mit `autonumber`, `video_autonumber` oder `epoch` erlauben keine verlässliche Dateizuordnung über den Namen: Ihre Zähler oder Extraktionszeitpunkte gehören zum jeweiligen Lauf, nicht zum Video. Bei diesen Vorlagen werden vorhandene Dateien nur über die in einem bestehenden Chat gespeicherte Quell-URL erkannt. Dateien ohne Chat werden nicht automatisch wiederverwendet und können erneut heruntergeladen werden. Für die automatische Erkennung solcher Dateien eignen sich stabile Namensfelder wie Titel und ID.

Beim nachträglichen Zuordnen einer Onlinequelle zu einem bestehenden Chat ist ein Einzelvideo-Link erforderlich. Sammlungslinks werden abgewiesen, ohne die gespeicherte Quelle zu ändern. Auch die HD-Tools weisen Sammlungen vor dem Mediendownload ab, einschließlich bereits von älteren Versionen gespeicherter Sammlungs-URLs.

Beim Start eines neuen Chats öffnen Playlist-Links vor dem Mediendownload eine Videoauswahl. Einträge mit fertiger Datei im Downloadordner und vorhandenem Chat werden ausgeblendet. Eine Datei ohne Chat bleibt als **file present, create chat** auswählbar: SceneAccess durchläuft den normalen Chat-Start mit dieser Datei, ohne sie erneut herunterzuladen. Für die ausgewählte vorhandene Datei werden die vollständigen Videoinformationen nachgeladen. Scheitert das, weist eine Warnung auf die eingeschränkten Informationen hin; die Datei bleibt nutzbar. Eine ausdrückliche Qualitätsänderung in **Advanced** fordert die neu gewählte Qualität an, statt stillschweigend die alte Datei weiterzuverwenden. Fehlende Dateien bleiben zum Download auswählbar. Die Zuordnung verwendet die von yt-dlp berechneten Dateinamen und die in Chats gespeicherten Quell-URLs, einschließlich der SceneAccess-Jobpräfixe. Beliebige Klammertexte im Titel gelten nicht als Videoidentität; bei bekannten widersprechenden Quellen oder mehrdeutigen Namen wird eine Datei nicht automatisch wiederverwendet. Es gibt keine dauerhafte Downloadhistorien-Datenbank. Enthält ein älterer benutzerdefinierter Dateiname keine ID und passt nicht mehr zu den aktuellen Namenseinstellungen, kann eine automatische Erkennung unmöglich sein.

Bekannte Einschränkung: Abgebrochene oder fehlgeschlagene native HLS-Downloads können Medienfragmente wie `.part-Frag1.part` im gewählten Downloadordner zurücklassen. SceneAccess weist nach Abbruch oder Fehler darauf hin. Entfernt werden nur ausdrücklich registrierte Dateien mit geprüfter Zugehörigkeit; es gibt keine pauschale Löschung passender Namen oder fremder Downloads. Die Fragmente können private Videodaten enthalten und Speicherplatz belegen. Der native HLS-Downloader bleibt bewusst erhalten.

Bei einem Abbruch unmittelbar nach dem HLS-Remux kann auch die neu erzeugte, vollständige MP4 im Downloadordner verbleiben, weil die Bereinigung eine geänderte Dateiidentität vorsichtshalber nicht löscht. Dies ist eine akzeptierte Bereinigungseinschränkung, kein erfolgreich abgeschlossener Download: Ein Abbruch garantiert nicht, dass alle neu erzeugten lokalen Videodaten entfernt werden. Verbliebene Dateien können private Inhalte enthalten und Speicherplatz belegen; bei Nichtbedarf manuell entfernen.

YouTube-Videos werden mit [yt-dlp](https://github.com/yt-dlp/yt-dlp) in einem Fortschrittsdialog heruntergeladen, der sich abbrechen lässt. Standardmäßig lädt SceneAccess die beste Qualität bis 480p – Details bleiben erkennbar, ohne HD-Dateigrößen – und fügt Bild und Ton zu einer MP4 zusammen, bevorzugt in H.264. In den **Settings** lässt sich zwischen 240p und 1080p wählen. Videos werden unter `scene-access` im Videos-Ordner gespeichert; gibt es diesen Ordner nicht, fragt SceneAccess nach einem Standard-Downloadordner, der sich auch in den **Settings** ändern lässt. Dateien heißen wie beim yt-dlp-Standard `Titel [ID].mp4`; unter **Settings → File name…** lässt sich per Kontrollkästchen jedes yt-dlp-Vorlagenfeld hinzunehmen, weitere Teile stehen in eckigen Klammern. Ein mitgeliefertes ffmpeg fügt Bild und Ton zusammen und schneidet Standbilder aus; ist Deno oder Node.js installiert, nutzt yt-dlp es für vollen YouTube-Support.

Verlangt YouTube „confirm you're not a bot“, fragt SceneAccess, wo du bei YouTube angemeldet bist, und versucht es mit den YouTube-Cookies dieses Browsers erneut; angemeldet bekommt yt-dlp außerdem mehr Videoinformationen. Firefox funktioniert direkt. Chrome und Edge verschlüsseln ihre Cookies so, dass yt-dlp sie unter Windows nicht lesen kann; für sie eine `cookies.txt` mit einer Browser-Erweiterung exportieren und auswählen. **Settings → YouTube sign-in** ändert die Wahl.

## Konfiguration und Datenschutz

SceneAccess verwendet `wx.StandardPaths.GetUserDataDir()` und damit den nativen Benutzer-Anwendungsdatenordner des jeweiligen Betriebssystems. Es gibt keine fest codierten Linux-, Windows- oder Hermes-Pfade.

OpenRouter wird immer unter `https://openrouter.ai/api/v1/chat/completions` erreicht. Die Adresse des llama.cpp-Servers muss ein vollständiger HTTP(S)-Chat-Completions-Endpunkt sein; ein abschließender Schrägstrich ist erlaubt. Seine Modellliste wird ausschließlich vom selben Ursprung und Pfadpräfix abgeleitet. Uneindeutige Adressen, Zugangsdaten in der URL, Query-Parameter und Fragmente werden abgelehnt. Es gibt keinen automatischen Rückfall von einem Anbieter auf den anderen.

`config.json` muss ein JSON-Objekt enthalten. Ungültiges JSON oder ein anderer Wurzeltyp beendet den Start mit einer Fehlermeldung einschließlich Dateipfad. Die Datei bleibt unverändert; SceneAccess setzt Einstellungen nicht stillschweigend zurück und überschreibt sie nicht.

Dort liegen:

- `config.json`: Einstellungen, Prompt-Profile und optional die API-Keys
- `chats/sceneaccess.db`: Titel, Anbieter, Modelle, Pfade, Prompts, Tools, Videoinformationen, Transkripte und Gesprächshistorie
- `chats/sessions/<id>/context.data-url`: vollständige rekonstruierbare Videokopie als Base64-Data-URL
- `chats/sessions/<id>/frame_*.jpg`: die Bilder, die das Modell angefordert hat
- `error.log`: unerwartete Fehler, die zusätzlich als Meldung erscheinen
- `instance.port` und plattformspezifische Sperrdateien: lokale Ein-Instanz-Koordination; keine API-Schlüssel oder Chatinhalte

Der API-Key kann alternativ über `OPENROUTER_API_KEY` gesetzt werden, diese Umgebungsvariable hat Vorrang. Base64 ist keine Verschlüsselung. Wer auf `context.data-url` zugreifen kann, besitzt eine Videokopie.

## Code-Struktur

Der gesamte Code liegt im Paket `sceneaccess`:

- `gui.py`: die Chatfenster, der gemeinsame Zustand aller Fenster (`ChatWindows`) und der App-Start
- `chat_list.py`: das Hauptfenster mit der Chatliste, Settings und dem Löschen eines Chats
- `app_window.py`: was Hauptfenster und Chatfenster teilen: Einstellungen, Modellkatalog und gespeicherte Chats
- `single_instance.py`: Prozesssperre und lokaler Aktivierungskanal mit festem Nachrichtentyp
- `new_chat.py`: einen Chat aus einer lokalen Datei oder von YouTube starten: Chatstart-Dialog, Frage zur Videogröße, Verkleinern und Download
- `dialogs.py`: Settings mit den Unterdialogen File name, Prompt profiles und Providers and models
- `chat_dialogs.py`: der Chatstart-Dialog mit Advanced…, die Fragen zur Videogröße, Chat settings und der Nachrichtenleser
- `chat_history.py`: die Einträge der Chat history und die Nutzungszeile
- `openrouter.py`: Anfragen an OpenRouter oder einen llama.cpp-Server, die Schleife der Tool-Aufrufe und die Websuche für llama.cpp-Chats
- `providers.py`: die beiden Anbieter und die feste Adresse von OpenRouter
- `tools.py`: die Tools für das Modell und der Code, der sie ausführt (ffmpeg-Standbilder, Ausschnitte in hoher Auflösung)
- `prompts.py`: der Grund-Prompt und die Standardprofile
- `models.py`: die Modell- und Anbieterliste von OpenRouter: welche Modelle Ton hören, und die Routing-Felder, die Anbieter und Modelle ausschließen
- `transcript.py`: das Transkript der Sprache eines Videos mit Zeitstempeln, und ob ein Chat eines braucht
- `video_size.py`: wie groß ein Video gesendet ist, und das Verkleinern mit ffmpeg
- `session_store.py`: Chats in SQLite und die privaten Videokopien
- `youtube.py`: yt-dlp-Downloads, Dateinamen und Videoinformationen
- `config.py`: Einstellungen in `config.json`
- `speech.py`: Ansagen über den laufenden Screenreader
- `cli.py`: die Kommandozeile, siehe Start aus dem Quellcode

`SceneAccess.py` ist der Starter, den PyInstaller verwendet. Die Tests liegen in `tests/`: eine Datei pro Modul (`prompts.py` wird in den Tests von Hauptfenster und Dialogen mitgeprüft), weitere Dateien für modulübergreifendes Verhalten (Lebensdauer und Steuerung von Anfragen, llama.cpp-Chats, Downloads und ihr Aufräumen, Arbeitsprozesse, Ein-Instanz-Betrieb, Start) sowie Prüfungen der Doku-Sprache, des Datenbanknamens, des Standardmodells und der Release-Dateien. `tests/conftest.py` enthält Fixtures, die mehrere Dateien teilen, etwa einen lokalen Video-Webserver, und lässt unter Windows alle Tests auf einem eigenen, unsichtbaren Desktop laufen, damit ihre Fenster weder auf dem Bildschirm erscheinen noch beim Screenreader ankommen.

`tools/` enthält Hilfsskripte für die Entwicklung: `guarded_pytest.py` startet pytest und bricht sofort ab, wenn ein Testfenster auf dem sichtbaren Desktop erscheint, `e2e_llama.py` schickt einen echten Chat an den llama.cpp-Server aus den Settings (oder an eine als Argument übergebene URL), und `live_window.ps1` führt einen Aufruf der Kommandozeile in einem eigenen Konsolenfenster aus, sodass er sich live verfolgen und mit Strg+C abbrechen lässt, und schreibt dieselbe Ausgabe in eine Logdatei; das Fenster schließt sich am Ende selbst.

Bekannte Lücken der Tests:

- Echte OpenRouter-Anfragen gehören nicht zu den Tests, weil sie kosten; sie werden von Hand geprüft. Die Tests verwenden stattdessen lokale HTTP-Server und vorgegebene Antworten. Anfragen an einen echten llama.cpp-Server werden ebenfalls nur von Hand geprüft.
- Das Laden eines Moments in hoher Auflösung von YouTube (`download_section`) braucht das Internet und hat keinen Test.
- Ansagen über einen echten Screenreader werden nur mit Attrappen getestet und von Hand mit NVDA geprüft.
- Private Dateirechte für `config.json` und die Chatdateien gibt es unter Linux und macOS; unter Windows werden diese Tests übersprungen, und die Dateien sind durch die Rechte des Benutzerprofils geschützt.
- Symlink-Tests werden unter Windows ohne Entwicklermodus oder Administratorrechte übersprungen; das Löschen eines Chats wird zusätzlich mit einer Verzeichnis-Junction getestet, die keine besonderen Rechte braucht.
- Die Tests für Doku und Release-Dateien prüfen nur, ob bestimmte Wörter vorkommen.

## Start aus dem Quellcode

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -p no:cacheprovider tests/ -q
python SceneAccess.py
```

Aus dem Quellcode hat SceneAccess auch eine Kommandozeile, die die Settings und Chats der App nutzt; `SceneAccess.exe` hat keine, weil ein Fensterprogramm keine Konsole hat:

```bash
python -m sceneaccess.cli chats
python -m sceneaccess.cli show "<Chat-Titel oder ID>"
python -m sceneaccess.cli compare "<Chat-Titel oder ID>" google/gemini-3.8-flash llama.cpp
```

- `chats` listet die Chats mit Anbieter, Modell und Transkriptmodus und markiert einen veralteten Systemprompt.
- `show` gibt Systemprompt und Verlauf eines Chats aus und markiert, was vor dem Modell versteckt ist. Es ändert nichts.
- `compare` beantwortet die erste Frage eines Chats nacheinander mit jedem Modell, so wie die App es heute täte: mit dem aktuellen Systemprompt, ohne den Verlauf des Chats und mit seinem Transkript, so wie es sein Transkriptmodus verlangt. Es arbeitet auf einer Kopie des Chats, der Chat bleibt also unverändert, und erstellt kein fehlendes Transkript. `llama.cpp` (oder `llama.cpp:<Modell>`) meint den eigenen Server; andere Namen sind OpenRouter-Modelle und kosten wie gewohnt. Ein Modell auf dem eigenen Server mit Toneingabe bekommt den Ton als Spur, wie in der App. `--sections SEKUNDEN` beschreibt ein längeres Video in Abschnitten, wie die App (0 sendet es ganz). `--transcribe auto|on|off` probiert für diesen Vergleich einen anderen Transkriptmodus, etwa `on`, damit ein hörendes Modell zusätzlich das Transkript mit seinen Zeiten bekommt. Die Antworten werden gestreamt: Gedankengang, Text und jeder Tool-Schritt werden geschrieben, sobald sie kommen, mit den Sekunden seit dem Start. So lässt sich die Ausgabe verfolgen, während ein langsames Modell noch arbeitet, und was vor einem Fehler kam, bleibt erhalten.

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
