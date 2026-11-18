# SceneAccess

SceneAccess is a native, accessible tool for conversations about videos, made for blind and visually impaired people. It sends a local or YouTube video to a vision model through OpenRouter or to your own llama.cpp server, keeps the video context locally, continues follow-up questions with a stable OpenRouter session ID, and lets the model look closer with tools.

[German translation](README.de.md)

## Usage

SceneAccess opens with its main window, the list of **Recent video chats**. **Enter** or a double click on a chat opens it in its own chat window; moving through the list with the arrow keys opens nothing.

1. In the main window, open **Settings**, choose the **provider for new chats**, and enter the OpenRouter model and API key, or the URL of your own llama.cpp server (see [Own llama.cpp server](#own-llamacpp-server)).
2. Choose **New video chat** in the main window, then **Start chat from local file** to select a local `.mp4`, `.webm`, or `.mov` file, or **Start chat from YouTube** to enter a link. A copied link is suggested automatically.
3. Enter the first question and choose a **prompt profile**. **Enter** starts the chat in a new chat window, **Shift+Enter** inserts a line break.
4. Ask follow-up questions in the chat window's question field. **Enter** sends the question, **Shift+Enter** inserts a line break.

The **Chat history** list contains one item per message. **Enter** on an item opens it in a read-only text field for reading line by line. Below every answer, a usage line shows the model that actually answered (useful with `openrouter/auto`), the prompt tokens read from the cache (hit), not served from the cache (miss) and written to the cache, and, when tools were used, the number of requests.

The answer is streamed: while the model thinks or writes, the Chat history shows an item **Model is thinking…** or **Model is writing…**, and **Enter** on it shows what has come so far. The item's text does not change while it grows, so the screen reader is not interrupted. Once the answer is stored, the model's thinking, if it shows any, stays as a folded item **Model's thinking (N words; Enter shows it)** with only its beginning; Enter shows all of it. It is not sent to the model again, and hiding or deleting the answer takes it along. If a request fails or is cancelled, what it had streamed stays in the list as **Unfinished, not saved** items until the list is built again, for example by the next question.

Several items of the Chat history can be selected: **Shift+arrow keys** extend the selection, **Ctrl+arrow keys** move without selecting and **Ctrl+Space** selects or deselects the current item. The context menu (Applications key, Shift+F10 or a right click) works on the selection. **Copy to clipboard** copies the full text of the selected items. The other entries work on the rounds of the selected answers: each round is its question, its tool steps with their images, and the answer. **Hide this answer from the model** (or **these answers**) keeps the rounds in the history, marked "[hidden from model]", but no longer sends them; **Show this answer to the model again** undoes that. **Delete this answer from the history** removes the rounds and their images after asking. Both are useful to compare the answers of different models to the same question, and both make the provider read the conversation from that point uncached once. The first question and the transcript belong to the video and stay; they can only be copied. While the chat is answering, its history cannot be changed, but it can be copied.

When a running screen reader such as NVDA is detected, SceneAccess announces that an answer is being generated, announces every tool the model runs on this computer, and moves the focus to the new answer when it arrives in the active chat window.

Direct announcements use only recognised screen-reader bridges. Available standalone synthesizers such as SAPI, ESpeak or Speech Dispatcher do not trigger speech; unknown output backends remain silent. No direct Orca announcement integration is included.

A chat remains usable if the original file is moved or deleted because the private local video context is retained.

## Main window and chat windows

The main window holds **New video chat**, **Settings**, the list of **Recent video chats** and **Delete chat**, which deletes the chat selected in the list after asking; its open chat window closes first. A chat that is still answering or saving is not deleted. Closing the main window ends SceneAccess and closes every chat window, cancelling running requests; finished chats stay stored.

Each chat opens in its own chat window, so Alt+Tab switches between chats. A chat window has the status line, the **Chat history**, the question field with **Send question** and **Pause request**, and the chat's own **Chat settings**, **Change prompt profile and tools** and **Update original file path**. Opening an already open chat brings its window back. The focus goes to the chat history, with the newest answer selected; closing a chat window returns it to the list. Settings are shared between all windows, and saved Settings show at once in open chat windows; a running request keeps the settings it started with. An answer arriving in a background window does not take focus from your current window.

Only one SceneAccess process runs per user application-data directory. Starting it again restores and activates the window used last instead of opening a second process that could overwrite chat history. If activation fails, the new process reports that SceneAccess is already running and exits without opening another instance.

Closing a chat cancels its request, but the chat remains protected against another request until its old worker has finished saving or stopping. Reopening it during that interval is allowed; a new question stays in the input field and can be retried shortly. History is appended in a SQLite write transaction, so completed turns cannot overwrite one another.

While a request runs, **Send question** becomes **Cancel request**. Cancelling interrupts the active API connection or local frame/audio ffmpeg process and prevents further model/tool calls. Already submitted work may still be billed by the provider. Network requests, high-resolution downloads and metadata probes run in isolated worker processes, with child-process cleanup on cancellation. Other preprocessing, such as an uncached model-catalog lookup, may need to return before cancellation finishes. An unsuccessful or cancelled question is restored in the input field if you have not already typed another question. Closing a chat window cancels its running request.

**Pause request** asks to pause after the current round: the model response and all local tool calls requested by that response finish, then no further model request is sent. **Resume request** continues the same in-memory turn without repeating completed tools. If the model has already given its final answer, the request completes instead of pausing. Closing the window discards a paused, unfinished turn; completed chat history remains stored.

**Settings → Maximum tool rounds per answer** accepts any nonnegative whole number, with a default of 6. A round can include several local tools, and the limit is followed by one final answer request with tool selection disabled. Zero omits all tools from the request, including hosted web search. Positive limits count local tool-call rounds, not searches executed internally by OpenRouter.

In **Start a video chat**, **Tools for this chat** has one checkbox per tool, all on by default; the high-resolution and video-information tools appear only for YouTube videos. **Deselect all tools** turns them all off and then becomes **Select all tools**. The choice is stored with the chat and does not become the default for other chats. Automatic audio transcription is preprocessing, not a model-selected tool, and remains available. **Change prompt profile and tools** explicitly replaces the chat's tool setup and can enable tools again.

## Prompts

Every chat starts with a fixed base prompt: describe for a blind person, check whether front-camera video is mirrored before saying left or right, give times as mm:ss, mark guesses as guesses, never invent brands or text, use the tools when a detail is too small, and answer in the user's language. A **prompt profile** chosen at the start of the chat adds instructions for a task. The defaults are General description, Audio description, Identify brand and model, and Read on-screen text. **Settings → Prompt profiles…** shows the base prompt and lets you edit, add and delete profiles.

A chat keeps the system prompt it started with, so its video stays cached, also when the base prompt or the default profiles improve later. While a chat still has an earlier version, or no system prompt at all because it is older than system prompts, its window shows **Update system prompt**. It rebuilds the prompt with the current base prompt and the chat's profile, with the profile's current default text if it still had an earlier one; a chat without a system prompt gets the base prompt. The chat keeps its tools and transcript. It asks first, because the next question sends the video uncached once; then the button disappears.

The prompt, the profile and the available tools are stored with each chat and do not follow later changes in Settings: they are sent before the video in every request, so changing them makes the provider cache the whole video again. **Change prompt profile and tools** changes them for the open chat on purpose, for example to give chats from before the tools existed the current prompt and tools. Older YouTube chats did not store their link: SceneAccess then offers the video ID from the file name, if it is there, and searches YouTube for the chat's title, so you can choose the video, enter a link, or say it is not a YouTube video. With the link, the chat also gets the high-resolution and video-information tools.

## Tools

The model decides itself when to use a tool. Every call and result of the tools that run on this computer appears in the Chat history while the model works, for example `Tool call: Zoom into 02:51, region x 0.4, y 0.5, width 0.2, height 0.1`.

- **Zoom into a frame**: a still image of one moment, optionally of a region, enlarged. From the original file, or from the stored copy if it was moved.
- **Load in high resolution** (YouTube chats): loads one second at that moment in up to 2160p and zooms into it. Useful because the chat video is downloaded in a lower resolution.
- **Read the video information** (YouTube chats): title, channel, upload date, description, tags and chapters, saved during the download.
- **Web search**: run by OpenRouter within the same request, so it is not shown or announced while it runs; the sources are listed below the answer. OpenRouter charges each search in addition to the tokens. A llama.cpp chat gets it only if chosen when the chat starts: it is then a tool on this computer, shown and announced like the others, that asks the OpenRouter model from Settings to search; only the search terms leave this computer.

Images are sent as a user message after the tool result, because many providers accept images only from the user. Models without tool support answer without tools; the status line then says so.

The cache is kept by each provider, not by OpenRouter. OpenRouter may send a request to a different provider of the same model, even with a fixed model, and then nothing is read from the cache; even on the same provider the cache is best-effort. The usage line therefore names the provider, for example `model z-ai/glm-5.3-flash via CoreWeave`.

## Sound and transcript

Provider exclusions also apply to automatic speech recognition. If ElevenLabs is excluded, SceneAccess does not send audio for transcription: the chat continues without a transcript, and its history explicitly records the reason. After allowing the provider again, **Change prompt profile and tools** can retry transcription.

Not every model hears a video's sound: Gemini models do, many others such as GLM only see the picture. SceneAccess reads OpenRouter's model list to find out. In the default transcript mode Auto (see below), if the chat's model cannot hear, or with `openrouter/auto`, where the answering model is not known in advance, the first question of a chat first sends the sound to ElevenLabs Scribe v2 through OpenRouter's transcription endpoint (about $0.11 per hour of sound). The transcript, with mm:ss times, goes with the video in every request and is cached with it. The Chat history shows a short Transcript item; **Enter** shows the full text. Automatic speech recognition can contain errors, and the model is told so. If transcription fails, the model answers without it, and **Change prompt profile and tools** tries again.

**Settings → Sound: With openrouter/auto, use only models that hear the video's sound** (off by default) limits `openrouter/auto` to models that take video with sound and support tools. New chats then need no transcript.

Each chat has a **Transcript of the speech** mode, the same for both providers:

- **Auto** makes and sends a transcript only while the chat's model cannot hear the video; a model that hears gets none. llama.cpp ignores the sound of a video. If the model on your server takes sound, as Qwen3-Omni does, its model list says so, and SceneAccess sends the video's sound along as a track of its own, extracted once and kept with the chat; the model then hears, and nothing goes to OpenRouter. A model on your server that takes no sound gets a transcript in Auto, like any model that cannot hear. SceneAccess asks the server before every question, so a server restarted with another model is handled right.
- **On** always makes and sends a transcript, also to models that hear.
- **Off** never sends the sound for a transcript. Models that hear still hear the video; the others get nothing about what is said.

**Settings → Transcript of the speech in new chats** sets the mode new chats start with (Auto by default), and **Start a video chat** can change it for the new chat. Existing chats keep their own mode; **Chat settings** changes it from the next question on. A transcript is only ever made through OpenRouter, so it needs an OpenRouter API key; it sends the video's sound, never the picture. A transcript the mode leaves out stays stored, so switching back never pays for it again.

Some models on your own server take only short videos and short sound; Gemma 4, for example, takes about 60 pictures and 30 seconds of sound. **Settings → llama.cpp: describe longer videos in sections of at most this many seconds** (0, the default, sends every video whole) lets new llama.cpp chats describe a longer video in sections of equal length, none longer than that, each cut precisely with ffmpeg and kept with the chat. Each section gets its own request with its own picture and sound, the part of the transcript that falls in it, and everything the model wrote about the sections before; the Chat history shows each section as it is done. Each description is then checked twice: mechanically, for lines that only repeat what is said and for times that are missing, out of order, outside the section or not mm:ss; and by the same model in a request of its own, without the video and without the chat, which also finds what is retold in other words. That request has a system prompt of its own, which tells the model it is the reviewer, what it checks and which rules a description must keep. **Settings → Sections: OpenRouter model that checks each section's description** lets another model check instead, e.g. a cheap one on OpenRouter; it gets only text, never the video, and needs the OpenRouter API key. Left empty, the chat's own model checks. `compare --reviewer MODEL` tries one for a comparison only. Only if either check finds something does the section go back to the model, with its video, its first description and what was found, to be described anew, once; the Chat history then shows the check with its findings and the first description. A last request, without the video, puts the descriptions together into the answer, keeping every detail and time. The sections are also kept joined without a model, leaving out a line where a section repeats the end of the one before, as **Sections joined without the model**. Each section's description is stored as soon as it is done, as **Section N (from to)** items, folded like the thinking: if the description stops, because it was cancelled, failed or the server restarted, sending the first question again continues with the next section. If the question, the model, the prompt, the section length or the transcript mode changed in between, it starts over. Later questions get the answer as text instead of the video, which is too long for the model; the zoom tools still look at the whole video. The setting moves to per-model settings later.

**Add a transcript of the speech** in the chat window makes a transcript later, through OpenRouter and ElevenLabs, and adds it at the end of the chat instead of before the video, so the cached video and history stay valid. It asks the model nothing; the next question sends it along, whatever the chat's transcript mode. The transcript shows in the Chat history and can be hidden from the model or deleted like an answer.

Only a model that takes in what is said, by hearing the video or from a transcript, gets the base prompt's instructions about it: to use what is said as context for which visible details matter, without quoting it unless asked. Only a model that hears also gets the instruction to mention other sounds. A model that gets neither, such as a llama.cpp model that takes no sound and has no transcript, is not confused by them.

## Providers and models

**Settings → Providers and models** opens a dialog with two lists. Providers checked in the first list (**Space** toggles one) never answer, whatever the model; the list comes from OpenRouter. The second list holds models that `openrouter/auto` never picks: **Add model…** finds one by typing part of its ID, and a pattern with `*` such as `google/*` excludes a whole family. A model chosen in **Settings → Model** is always used, but its excluded providers still don't answer.

Providers limit how large a request may be, and SceneAccess sends the whole video in every request. Tested in October 2026 through OpenRouter: Google AI Studio rejects requests of more than 20,000,000 bytes, so a video of more than about 19 MB once Base64-encoded (a file of about 14 MB), often with nothing more than "HTTP 400: Invalid content". Google Vertex rejected 29.8 MB Base64 and more; nothing between that and 0.1 MB was tested. Qwen allows 20 MiB Base64, Xiaomi MiMo took 30 MB Base64, and Meta's Muse Wonder takes files of up to 50 MiB (before Base64).

## Own llama.cpp server

**Settings → Provider for new chats** chooses between OpenRouter and your own [llama.cpp](https://github.com/ggml-org/llama.cpp) server. Each chat keeps the provider it started with, so a chat started on your server never sends its video to OpenRouter, even after Settings change. The status line names the chat's provider.

**Chat settings → Provider of this chat** moves a chat to the other provider. The chat then follows the new provider's model from Settings until it gets its own, keeps its chosen tools with that provider's web search, and keeps an existing transcript; a skipped transcript is tried again on the next question. Moving a chat from your own server to OpenRouter asks first, because from the next question on its video goes to OpenRouter, and, unless its transcript mode is Off, its sound to ElevenLabs when a transcript is needed. Chat settings also changes the chat's **Transcript of the speech** mode. Chat settings waits at most about five seconds for your server's model list; without it, a model can still be typed.

For a llama.cpp chat, enter the server's chat-completions URL, for example `http://192.168.1.20:8080/v1/chat/completions`, an optional model name (a server with one model answers whatever the name), and the optional key of `llama-server --api-key`. Only that key is sent to the server, never the OpenRouter key. The server needs a vision model with its `--mmproj` file, video support (llama.cpp from June 2026, with ffmpeg and ffprobe installed; on Windows a build from after 24 August 2026), and `--jinja` for the tools. OpenRouter's routing, provider exclusions and hosted web search are not used for it.

A llama.cpp chat uses OpenRouter only for two things, both only with an OpenRouter API key: the tool **Web search through OpenRouter**, chosen in **Start a video chat**, sends only search terms, and a transcript, as its transcript mode asks (see Sound and transcript), sends the video's sound. **Chat settings** lists the models your server offers.

## Video size

Before a chat starts with a video that would be more than 19 MB once sent, SceneAccess explains the sizes and asks: **Shrink the video**, **Request a lower resolution** (YouTube only), **Send as it is**, or **Back to Start a video chat**. Shrinking makes a copy with ffmpeg that fits into 16 MB, leaving room for the frames the model asks for, which stay in the chat and are sent again with every later question. With little room the copy keeps 5 frames per second and a sharper picture rather than smooth motion, as Gemini samples one frame per second by default, and the sound stays stereo. Only the copy is sent and stored with the chat; the original file is not changed, and while it is available, frames and the transcript still come from it. A lower resolution downloads the video again one quality step lower and keeps stepping down until it fits. Each GUI download gets a unique job prefix in the same target folder, so it cannot silently reuse an older, higher-quality file. Previous complete downloads are retained, including if a replacement fails or is cancelled. Aborted downloads clean up only explicitly registered, identity-matching job files; unrelated files are never selected by directory differences or filename prefixes.

With a model other than `openrouter/auto`, **Don't show this dialog again** skips the question for that model once a large video was answered. **Settings → Video size** turns the question off completely (too large videos then fail) or asks again for skipped models. **Advanced…** in the Start a video chat dialog sets the YouTube quality and can shrink too large videos automatically, without asking, to a size of your choice.

Chats started before this check may store a larger video. Before a question in such a chat, SceneAccess offers to **Shrink the stored video** (the next question then sends it uncached once, so it costs more), to open **Chat settings…**, for example to let `openrouter/auto` pick a model that only sees the picture and gets a transcript, or to **Send as it is**. Once a question sent as it is was answered, the chat stops asking until its video or its Chat settings change.

## Chat settings

**Chat settings** sets the open chat's model. A new chat uses the model from Settings, and follows it when Settings changes (if the chat's transcript mode then asks for a transcript it does not have, its next question first makes one, about $0.11 per hour of sound, and sends the video uncached once); unchecking **Use the model from Settings** gives the chat its own model, typed or found with **Find model…**. The chat's own **With openrouter/auto, use only models that hear the video's sound** checkbox starts with the value from Settings. **Shrink the stored video** makes a smaller copy for this chat. A new model or a shrunk video makes the next question send the video uncached once; a chat without a transcript checks again whether its new model needs one.

## YouTube

Filename templates containing `autonumber`, `video_autonumber` or `epoch` cannot identify existing files by name reliably: their counters or extraction timestamps belong to an individual run, not to a video. For these templates, completed files are recognised only through an existing chat's saved source URL. Files without a chat are not automatically reused and may be downloaded again. Choose stable filename fields such as title and ID for automatic orphan-file recognition.

When assigning an online source to an existing chat, enter a single-video link. Collection links are rejected without changing the saved source. HD tools also reject collections before downloading media, including collection URLs stored by older versions.

When starting a new chat, playlist links open a video selection before any media is downloaded. Entries with both a completed file in the download folder and an existing chat are hidden. A file without a chat remains selectable as **file present, create chat**: SceneAccess starts the normal chat setup using that file, without downloading it again. Full video information is loaded for the selected existing file; if that lookup fails, a warning explains that the file can still be used with limited information. Explicit quality changes in **Advanced** request the newly chosen quality instead of silently retaining the old file. Missing files remain selectable for download. File matching uses yt-dlp's expected filenames and saved chat source URLs, including SceneAccess job prefixes. Arbitrary bracketed title text is never treated as a video identity, and known conflicting sources or ambiguous names are not reused automatically; there is no permanent download-history database. If an older custom filename contains no ID and no longer matches the current naming settings, automatic recognition may not be possible.

Known limitation: cancelled or failed native HLS downloads can leave media fragments such as `.part-Frag1.part` in the chosen download folder. SceneAccess reports this after cancellation or failure. Only explicitly registered files with verified ownership are removed; it does not sweep matching names or other downloads. Those fragments can contain private video data and consume disk space. The native HLS downloader is deliberately retained.

Cancellation immediately after HLS remuxing can also leave the newly created, complete MP4 in the download folder because cleanup conservatively refuses a changed file identity. This is an accepted cleanup limitation, not a successful download: cancellation does not guarantee removal of all newly created local video data. Retained files may contain private content and use disk space; remove them manually if no longer needed.

YouTube videos are downloaded with [yt-dlp](https://github.com/yt-dlp/yt-dlp) in a progress dialog that can be cancelled. By default SceneAccess downloads the best quality up to 480p, which keeps details recognizable without HD file sizes, with video and audio merged into an MP4 (preferring H.264). **Settings** lets you choose between 240p and 1080p. Videos are saved to `scene-access` in your Videos folder; if that folder does not exist, SceneAccess asks for a default download folder, which can also be changed in **Settings**. Files are named like yt-dlp's default, `Title [ID].mp4`; **Settings → File name…** lets you check any yt-dlp output template field to include, with further parts added in brackets. A bundled ffmpeg merges video and audio and extracts frames; if Deno or Node.js is installed, yt-dlp uses it for full YouTube support.

When YouTube asks to "confirm you're not a bot", SceneAccess asks where you are signed in to YouTube and tries again with that browser's YouTube cookies; signed in, yt-dlp also gets more video information. Firefox works directly. Chrome and Edge encrypt their cookies so that yt-dlp cannot read them on Windows, so for them, export a `cookies.txt` file with a browser extension and choose it. **Settings → YouTube sign-in** changes the choice.

## Configuration and privacy

SceneAccess uses `wx.StandardPaths.GetUserDataDir()` and therefore the native per-user application-data directory of the operating system. No Linux, Windows, or Hermes paths are hard-coded.

OpenRouter is always reached at `https://openrouter.ai/api/v1/chat/completions`. The llama.cpp server URL must be a complete HTTP(S) chat-completions endpoint; a trailing slash is accepted. Its model list is derived only from that same origin and prefix. Ambiguous addresses, URL credentials, queries and fragments are rejected. There is no automatic fallback from one provider to the other.

`config.json` must contain a JSON object. Invalid JSON or a non-object root stops startup with an error showing the file path. The file is left unchanged; SceneAccess does not silently reset your settings or overwrite it.

That directory contains:

- `config.json`: settings, prompt profiles, and optionally the API keys
- `chats/sceneaccess.db`: titles, providers, models, paths, prompts, tools, video information, transcripts, and conversation histories
- `chats/sessions/<id>/context.data-url`: a complete reconstructable video copy as a Base64 data URL
- `chats/sessions/<id>/frame_*.jpg`: the images the model asked for
- `error.log`: unexpected errors, which are also shown in a message
- `instance.port` and platform-specific lock files: local single-instance coordination; no API keys or chat contents

Alternatively, set `OPENROUTER_API_KEY`; the environment variable takes precedence. Base64 is not encryption. Anyone who can access `context.data-url` has a copy of the video.

## Code structure

All code is in the `sceneaccess` package:

- `gui.py`: the chat windows, the windows' shared state (`ChatWindows`) and the app start
- `chat_list.py`: the main window with the list of chats, Settings and deleting a chat
- `app_window.py`: what the main window and the chat windows share: settings, model catalogue and stored chats
- `new_chat.py`: starting a chat from a local file or YouTube: the start dialog, the video size question, shrinking and the download
- `dialogs.py`: Settings with its File name, Prompt profiles and Providers and models sub-dialogs
- `chat_dialogs.py`: the start-chat dialog with Advanced…, the video size questions, Chat settings, and the message reader
- `chat_history.py`: the lines of the Chat history list and the usage line
- `openrouter.py`: requests to OpenRouter or a llama.cpp server, the tool-call loop, and the web search for llama.cpp chats
- `providers.py`: the two providers and OpenRouter's fixed address
- `request_control.py`: cancellation and pause/resume boundaries
- `single_instance.py`: process locking and the fixed-message local window-activation channel
- `worker_process.py`: interruptible network/media worker processes and child-process cleanup
- `tools.py`: the tools offered to the model and the code that runs them (ffmpeg frames, high-resolution sections)
- `prompts.py`: the base prompt and the default profiles
- `models.py`: OpenRouter's model and provider lists: which models hear sound, and the routing fields that exclude providers and models
- `transcript.py`: the timestamped transcript of a video's speech, and whether a chat needs one
- `video_size.py`: how large a video is once sent, and shrinking it with ffmpeg
- `session_store.py`: chats in SQLite and the private video copies
- `youtube.py`: yt-dlp downloads, file names and video information
- `config.py`: settings in `config.json`
- `speech.py`: announcements through the running screen reader
- `cli.py`: the command line, see Run from source

`SceneAccess.py` is the launcher used by PyInstaller. Tests are in `tests/`: one file per module (`prompts.py` is covered by the main window and dialog tests), further files for behaviour that spans modules (request lifetime and controls, llama.cpp chats, downloads and their cleanup, worker processes, the single instance, startup), and checks of the documentation language, the database name, the default model and the release files. `tests/conftest.py` holds fixtures several files share, such as a local video web server, and on Windows runs all tests on a separate invisible desktop, so their windows never appear on the screen or reach the screen reader.

`tools/` holds helper scripts for development: `guarded_pytest.py` runs pytest and stops it at once if a test window appears on the visible desktop, `e2e_llama.py` sends a real chat to the llama.cpp server set in the settings (or to a URL given as argument), and `live_window.ps1` runs a command line call in a console window of its own, so it can be followed live and cancelled with Ctrl+C, while writing the same output to a log file; the window closes when the call ends.

Known gaps in the tests:

- Real OpenRouter requests are not part of the tests, because they are billed; they are checked by hand. The tests use local HTTP servers and recorded responses instead. Requests to a real llama.cpp server are also checked by hand only.
- Loading a moment in high resolution from YouTube (`download_section`) needs the internet and has no test.
- Announcements through a real screen reader are tested with fakes only and checked by hand with NVDA.
- Private file permissions for `config.json` and the chat files are a Linux and macOS feature; on Windows these tests are skipped, and the files rely on the permissions of the user profile.
- Symlink tests are skipped on Windows without Developer Mode or administrator rights; deleting a chat is also tested with a directory junction, which needs no special rights.
- The documentation and release-file tests only check that certain words appear.

## Run from source

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -p no:cacheprovider tests/ -q
python SceneAccess.py
```

From source, SceneAccess also has a command line, which uses the app's settings and chats; `SceneAccess.exe` has none, as a windowed program has no console:

```bash
python -m sceneaccess.cli chats
python -m sceneaccess.cli show "<chat title or ID>"
python -m sceneaccess.cli compare "<chat title or ID>" google/gemini-3.8-flash llama.cpp
```

- `chats` lists the chats with their provider, model and transcript mode, and marks an outdated system prompt.
- `show` prints a chat's system prompt and history, marking what is hidden from the model. It changes nothing.
- `compare` answers a chat's first question with each model in turn, as the app would today: with the current system prompt, without the chat's history, and with its transcript as its transcript mode asks. It works on a copy of the chat, so the chat stays unchanged, and it makes no missing transcript. `llama.cpp` (or `llama.cpp:<model>`) means your own server; other names are OpenRouter models, billed as usual. A model on your server that takes sound gets it as a track, as in the app. `--sections SECONDS` describes a longer video in sections, as the app does (0 sends it whole). `--transcribe auto|on|off` tries another transcript mode for this comparison only, for example `on` to give a hearing model the transcript with its times as well. The answers are streamed: the thinking, the text and every tool step are written as they come, with the seconds since the start, so the output can be followed while a slow model is still at it, and what came before a failure stays.

## Windows package through GitHub Actions

The workflow in `.github/workflows/windows-release.yml` runs on a real Windows runner. It tests the source, builds `SceneAccess.exe` with PyInstaller in `--onedir` mode, and uploads a `SceneAccess-windows-x64.zip` artifact. A pushed `v*` tag publishes it as a GitHub release, a `nightly-YYYY-MM-DD` tag as a pre-release.

After extraction, the ZIP contains these entries directly:

```text
SceneAccess.exe
_internal/
sceneaccess/
tests/
README.md
README.de.md
...
```

There is no additional `windows/` directory and no self-extracting executable. `_internal` must remain beside `SceneAccess.exe` because it contains the Python and wxPython runtime.
