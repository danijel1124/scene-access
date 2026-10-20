# SceneAccess

SceneAccess is a native, accessible tool for conversations about videos, made for blind and visually impaired people. It sends a local or YouTube video to a vision model through OpenRouter, keeps the video context locally, continues follow-up questions with a stable OpenRouter session ID, and lets the model look closer with tools.

[German translation](README.de.md)

## Usage

1. Open **Settings** and enter a model, base URL, and optionally an API key.
2. Choose **New video chat**, then **Start chat from local file** to select a local `.mp4`, `.webm`, or `.mov` file, or **Start chat from YouTube** to enter a link. A copied link is suggested automatically.
3. Enter the first question and choose a **prompt profile**. **Enter** starts the chat, **Shift+Enter** inserts a line break.
4. Ask follow-up questions in the question field. **Enter** sends the question, **Shift+Enter** inserts a line break.

The **Chat history** list contains one item per message. **Enter** on an item opens it in a read-only text field for reading line by line. Below every answer, a usage line shows the model that actually answered (useful with `openrouter/auto`), the prompt tokens read from the cache (hit), not served from the cache (miss) and written to the cache, and, when tools were used, the number of requests.

When a running screen reader such as NVDA is detected, SceneAccess announces that an answer is being generated, announces every tool the model runs on this computer, and moves the focus to the new answer when it arrives in the active chat window.

The sidebar lists recent chats. A chat remains usable if the original file is moved or deleted because the private local video context is retained.

## Chat windows and request controls

Each chat opens in its own top-level window, so Alt+Tab switches between chats. Opening an already open chat raises its existing window. Settings are shared between windows; a running request keeps the settings it started with. An answer arriving in a background window does not take focus from your current window.

Only one SceneAccess process runs per user application-data directory. Starting it again restores and activates the last active chat window instead of opening a second process that could overwrite chat history. If activation fails, the new process reports that SceneAccess is already running and exits without opening another instance.

Closing a chat cancels its request, but the chat remains protected against another request until its old worker has finished saving or stopping. Reopening it during that interval is allowed; a new question stays in the input field and can be retried shortly. History is appended in a SQLite write transaction, so completed turns cannot overwrite one another.

While a request runs, **Send question** becomes **Cancel request**. Cancelling interrupts the active API connection or local frame/audio ffmpeg process and prevents further model/tool calls. Already submitted work may still be billed by the provider. Network requests, high-resolution downloads and metadata probes run in isolated worker processes, with child-process cleanup on cancellation. Other preprocessing, such as an uncached model-catalog lookup, may need to return before cancellation finishes. An unsuccessful or cancelled question is restored in the input field if you have not already typed another question. Closing a chat window cancels its running request.

**Pause request** asks to pause after the current round: the model response and all local tool calls requested by that response finish, then no further model request is sent. **Resume request** continues the same in-memory turn without repeating completed tools. If the model has already given its final answer, the request completes instead of pausing. Closing the window discards a paused, unfinished turn; completed chat history remains stored.

**Settings → Maximum tool rounds per answer** accepts any nonnegative whole number, with a default of 6. A round can include several local tools, and the limit is followed by one final answer request with tool selection disabled. Zero omits all tools from the request, including hosted web search. Positive limits count local tool-call rounds, not searches executed internally by OpenRouter.

In **Start a video chat**, **Disable all tools for this chat** omits both local tools and hosted web search. This choice is stored with the chat and does not become the default for other chats. Automatic audio transcription is preprocessing, not a model-selected tool, and remains available. **Change prompt profile and tools** explicitly replaces the chat's tool setup and can enable tools again.

## Prompts

Every chat starts with a fixed base prompt: describe for a blind person, give times as mm:ss, mark guesses as guesses, never invent brands or text, use the tools when a detail is too small, and answer in the user's language. A **prompt profile** chosen at the start of the chat adds instructions for a task. The defaults are General description, Audio description, Identify brand and model, and Read on-screen text. **Settings → Prompt profiles…** shows the base prompt and lets you edit, add and delete profiles.

The prompt, the profile and the available tools are stored with each chat and do not follow later changes in Settings: they are sent before the video in every request, so changing them makes the provider cache the whole video again. **Change prompt profile and tools** changes them for the open chat on purpose, for example to give chats from before the tools existed the current prompt and tools. Older YouTube chats did not store their link: SceneAccess then offers the video ID from the file name, if it is there, and searches YouTube for the chat's title, so you can choose the video, enter a link, or say it is not a YouTube video. With the link, the chat also gets the high-resolution and video-information tools.

## Tools

The model decides itself when to use a tool. Every call and result of the tools that run on this computer appears in the Chat history while the model works, for example `Tool call: Zoom into 02:51, region x 0.4, y 0.5, width 0.2, height 0.1`.

- **Zoom into a frame**: a still image of one moment, optionally of a region, enlarged. From the original file, or from the stored copy if it was moved.
- **Load in high resolution** (YouTube chats): loads one second at that moment in up to 2160p and zooms into it. Useful because the chat video is downloaded in a lower resolution.
- **Read the video information** (YouTube chats): title, channel, upload date, description, tags and chapters, saved during the download.
- **Web search**: run by OpenRouter within the same request, so it is not shown or announced while it runs; the sources are listed below the answer. OpenRouter charges each search in addition to the tokens.

Images are sent as a user message after the tool result, because many providers accept images only from the user. Models without tool support answer without tools; the status line then says so.

The cache is kept by each provider, not by OpenRouter. OpenRouter may send a request to a different provider of the same model, even with a fixed model, and then nothing is read from the cache; even on the same provider the cache is best-effort. The usage line therefore names the provider, for example `model z-ai/glm-5.3-flash via CoreWeave`.

## Sound and transcript

Provider exclusions also apply to automatic speech recognition. If ElevenLabs is excluded, SceneAccess does not send audio for transcription: the chat continues without a transcript, and its history explicitly records the reason. After allowing the provider again, **Change prompt profile and tools** can retry transcription.

Not every model hears a video's sound: Gemini models do, many others such as GLM only see the picture. SceneAccess reads OpenRouter's model list to find out. If the chat's model cannot hear, or with `openrouter/auto`, where the answering model is not known in advance, the first question of a chat first sends the sound to ElevenLabs Scribe v2 through OpenRouter's transcription endpoint (about $0.11 per hour of sound). The transcript, with mm:ss times, goes with the video in every request and is cached with it. The Chat history shows a short Transcript item; **Enter** shows the full text. Automatic speech recognition can contain errors, and the model is told so. If transcription fails, the model answers without it, and **Change prompt profile and tools** tries again.

**Settings → Sound: With openrouter/auto, use only models that hear the video's sound** (off by default) limits `openrouter/auto` to models that take video with sound and support tools. New chats then need no transcript.

## Providers and models

**Settings → Providers and models** opens a dialog with two lists. Providers checked in the first list (**Space** toggles one) never answer, whatever the model; the list comes from OpenRouter. The second list holds models that `openrouter/auto` never picks: **Add model…** finds one by typing part of its ID, and a pattern with `*` such as `google/*` excludes a whole family. A model chosen in **Settings → Model** is always used, but its excluded providers still don't answer.

Providers limit how large a request may be, and SceneAccess sends the whole video in every request. Tested in October 2026 through OpenRouter: Google AI Studio rejects requests of more than 20,000,000 bytes, so a video of more than about 19 MB once Base64-encoded (a file of about 14 MB), often with nothing more than "HTTP 400: Invalid content". Google Vertex rejected 29.8 MB Base64 and more; nothing between that and 0.1 MB was tested. Qwen allows 20 MiB Base64, Xiaomi MiMo took 30 MB Base64, and Meta's Muse Wonder takes files of up to 50 MiB (before Base64).

## Video size

Before a chat starts with a video that would be more than 19 MB once sent, SceneAccess explains the sizes and asks: **Shrink the video**, **Request a lower resolution** (YouTube only), **Send as it is**, or **Back to Start a video chat**. Shrinking makes a copy with ffmpeg that fits into 16 MB, leaving room for the frames the model asks for, which stay in the chat and are sent again with every later question. With little room the copy keeps 5 frames per second and a sharper picture rather than smooth motion, as Gemini samples one frame per second by default, and the sound stays stereo. Only the copy is sent and stored with the chat; the original file is not changed, and while it is available, frames and the transcript still come from it. A lower resolution downloads the video again one quality step lower and keeps stepping down until it fits. Each GUI download gets a unique job prefix in the same target folder, so it cannot silently reuse an older, higher-quality file. Previous complete downloads are retained, including if a replacement fails or is cancelled. Aborted downloads clean up only explicitly registered, identity-matching job files; unrelated files are never selected by directory differences or filename prefixes.

With a model other than `openrouter/auto`, **Don't show this dialog again** skips the question for that model once a large video was answered. **Settings → Video size** turns the question off completely (too large videos then fail) or asks again for skipped models. **Advanced…** in the Start a video chat dialog sets the YouTube quality and can shrink too large videos automatically, without asking, to a size of your choice.

Chats started before this check may store a larger video. Before a question in such a chat, SceneAccess offers to **Shrink the stored video** (the next question then sends it uncached once, so it costs more), to open **Chat settings…**, for example to let `openrouter/auto` pick a model that only sees the picture and gets a transcript, or to **Send as it is**. Once a question sent as it is was answered, the chat stops asking until its video or its Chat settings change.

## Chat settings

**Chat settings** sets the open chat's model. A new chat uses the model from Settings, and follows it when Settings changes (if the new model cannot hear, the chat's next question first makes a transcript, about $0.11 per hour of sound, and sends the video uncached once); unchecking **Use the model from Settings** gives the chat its own model, typed or found with **Find model…**. The chat's own **With openrouter/auto, use only models that hear the video's sound** checkbox starts with the value from Settings. **Shrink the stored video** makes a smaller copy for this chat. A new model or a shrunk video makes the next question send the video uncached once; a chat without a transcript checks again whether its new model needs one.

## YouTube

Known limitation: cancelled or failed native HLS downloads can leave media fragments such as `.part-Frag1.part` in the chosen download folder. SceneAccess reports this after cancellation or failure. Only explicitly registered files with verified ownership are removed; it does not sweep matching names or other downloads. Those fragments can contain private video data and consume disk space. The native HLS downloader is deliberately retained.

YouTube videos are downloaded with [yt-dlp](https://github.com/yt-dlp/yt-dlp) in a progress dialog that can be cancelled. By default SceneAccess downloads the best quality up to 480p, which keeps details recognizable without HD file sizes, with video and audio merged into an MP4 (preferring H.264). **Settings** lets you choose between 240p and 1080p. Videos are saved to `scene-access` in your Videos folder; if that folder does not exist, SceneAccess asks for a default download folder, which can also be changed in **Settings**. Files are named like yt-dlp's default, `Title [ID].mp4`; **Settings → File name…** lets you check any yt-dlp output template field to include, with further parts added in brackets. A bundled ffmpeg merges video and audio and extracts frames; if Deno or Node.js is installed, yt-dlp uses it for full YouTube support.

When YouTube asks to "confirm you're not a bot", SceneAccess asks where you are signed in to YouTube and tries again with that browser's YouTube cookies; signed in, yt-dlp also gets more video information. Firefox works directly. Chrome and Edge encrypt their cookies so that yt-dlp cannot read them on Windows, so for them, export a `cookies.txt` file with a browser extension and choose it. **Settings → YouTube sign-in** changes the choice.

## Configuration and privacy

SceneAccess uses `wx.StandardPaths.GetUserDataDir()` and therefore the native per-user application-data directory of the operating system. No Linux, Windows, or Hermes paths are hard-coded.

The API base URL must be a complete HTTP(S) chat-completions endpoint, such as `https://openrouter.ai/api/v1/chat/completions`; a trailing slash is accepted. Model, provider and audio-transcription endpoints are derived only from that same API origin and prefix. Ambiguous addresses, URL credentials, queries and fragments are rejected. There is no automatic fallback to another API provider; a custom endpoint must support the requested API.

`config.json` must contain a JSON object. Invalid JSON or a non-object root stops startup with an error showing the file path. The file is left unchanged; SceneAccess does not silently reset your settings or overwrite it.

That directory contains:

- `config.json`: settings, prompt profiles, and optionally the API key
- `chats/sceneaccess.db`: titles, models, paths, prompts, tools, video information, transcripts, and conversation histories
- `chats/sessions/<id>/context.data-url`: a complete reconstructable video copy as a Base64 data URL
- `chats/sessions/<id>/frame_*.jpg`: the images the model asked for
- `error.log`: unexpected errors, which are also shown in a message
- `instance.port` and platform-specific lock files: local single-instance coordination; no API keys or chat contents

Alternatively, set `OPENROUTER_API_KEY`; the environment variable takes precedence. Base64 is not encryption. Anyone who can access `context.data-url` has a copy of the video.

## Code structure

All code is in the `sceneaccess` package:

- `gui.py`: the main window and the app start
- `new_chat.py`: starting a chat from a local file or YouTube: the start dialog, the video size question, shrinking and the download
- `dialogs.py`: Settings with its File name, Prompt profiles and Providers and models sub-dialogs
- `chat_dialogs.py`: the start-chat dialog with Advanced…, the video size questions, Chat settings, and the message reader
- `chat_history.py`: the lines of the Chat history list and the usage line
- `openrouter.py`: requests to OpenRouter and the tool-call loop
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

`SceneAccess.py` is the launcher used by PyInstaller. Tests are in `tests/`: one file per module (`prompts.py` is covered by the main window and dialog tests), further files for behaviour that spans modules (request lifetime and controls, downloads and their cleanup, worker processes, the single instance, startup), and checks of the documentation language, the database name, the default model and the release files.

Known gaps in the tests:

- Real OpenRouter requests are not part of the tests, because they are billed; they are checked by hand. The tests use local HTTP servers and recorded responses instead.
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
