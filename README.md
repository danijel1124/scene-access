# SceneAccess

SceneAccess is a native, accessible tool for conversations about videos, made for blind and visually impaired people. It sends a local or YouTube video to a vision model through OpenRouter, keeps the video context locally, continues follow-up questions with a stable OpenRouter session ID, and lets the model look closer with tools.

[German translation](README.de.md)

## Usage

1. Open **Settings** and enter a model, base URL, and optionally an API key.
2. Choose **New video chat**, then **Start chat from local file** to select a local `.mp4`, `.webm`, or `.mov` file, or **Start chat from YouTube** to enter a link. A copied link is suggested automatically.
3. Enter the first question and choose a **prompt profile**. **Enter** starts the chat, **Shift+Enter** inserts a line break.
4. Ask follow-up questions in the question field. **Enter** sends the question, **Shift+Enter** inserts a line break.

The **Chat history** list contains one item per message. **Enter** on an item opens it in a read-only text field for reading line by line. Below every answer, a usage line shows the model that actually answered (useful with `openrouter/auto`), the prompt tokens read from the cache (hit), not served from the cache (miss) and written to the cache, and, when tools were used, the number of requests.

When a running screen reader such as NVDA is detected, SceneAccess announces that an answer is being generated, announces every tool the model runs on this computer, and moves the focus to the new answer when it arrives.

The sidebar lists recent chats. A chat remains usable if the original file is moved or deleted because the private local video context is retained.

## Prompts

Every chat starts with a fixed base prompt: describe for a blind person, give times as mm:ss, mark guesses as guesses, never invent brands or text, use the tools when a detail is too small, and answer in the user's language. A **prompt profile** chosen at the start of the chat adds instructions for a task. The defaults are General description, Audio description, Identify brand and model, and Read on-screen text. **Settings → Prompt profiles…** shows the base prompt and lets you edit, add and delete profiles.

The prompt, the profile and the available tools are stored with each chat and do not follow later changes in Settings: they are sent before the video in every request, so changing them makes the provider cache the whole video again. **Change prompt profile and tools** changes them for the open chat on purpose, for example to give chats from before the tools existed the current prompt and tools. Older YouTube chats did not store their link, so they get zoom and web search only.

## Tools

The model decides itself when to use a tool. Every call and result of the tools that run on this computer appears in the Chat history while the model works, for example `Tool call: Zoom into 02:51, region x 0.4, y 0.5, width 0.2, height 0.1`.

- **Zoom into a frame**: a still image of one moment, optionally of a region, enlarged. From the original file, or from the stored copy if it was moved.
- **Load in high resolution** (YouTube chats): loads one second at that moment in up to 2160p and zooms into it. Useful because the chat video is downloaded in a lower resolution.
- **Read the video information** (YouTube chats): title, channel, upload date, description, tags and chapters, saved during the download.
- **Web search**: run by OpenRouter within the same request, so it is not shown or announced while it runs; the sources are listed below the answer. OpenRouter charges each search in addition to the tokens.

Images are sent as a user message after the tool result, because many providers accept images only from the user. Models without tool support answer without tools; the status line then says so.

The cache is kept by each provider, not by OpenRouter. OpenRouter may send a request to a different provider of the same model, even with a fixed model, and then nothing is read from the cache; even on the same provider the cache is best-effort. The usage line therefore names the provider, for example `model z-ai/glm-5.3-flash via CoreWeave`.

## YouTube

YouTube videos are downloaded with [yt-dlp](https://github.com/yt-dlp/yt-dlp) in a progress dialog that can be cancelled. By default SceneAccess downloads the best quality up to 480p, which keeps details recognizable without HD file sizes, with video and audio merged into an MP4 (preferring H.264). **Settings** lets you choose between 240p and 1080p. Videos are saved to `scene-access` in your Videos folder; if that folder does not exist, SceneAccess asks for a default download folder, which can also be changed in **Settings**. Files are named like yt-dlp's default, `Title [ID].mp4`; **Settings → File name…** lets you check any yt-dlp output template field to include, with further parts added in brackets. A bundled ffmpeg merges video and audio and extracts frames; if Deno or Node.js is installed, yt-dlp uses it for full YouTube support.

## Configuration and privacy

SceneAccess uses `wx.StandardPaths.GetUserDataDir()` and therefore the native per-user application-data directory of the operating system. No Linux, Windows, or Hermes paths are hard-coded.

That directory contains:

- `config.json`: settings, prompt profiles, and optionally the API key
- `chats/sceneaccess.db`: titles, models, paths, prompts, tools, video information, and conversation histories
- `chats/sessions/<id>/context.data-url`: a complete reconstructable video copy as a Base64 data URL
- `chats/sessions/<id>/frame_*.jpg`: the images the model asked for
- `error.log`: unexpected errors, which are also shown in a message

Alternatively, set `OPENROUTER_API_KEY`; the environment variable takes precedence. Base64 is not encryption. Anyone who can access `context.data-url` has a copy of the video.

## Code structure

All code is in the `sceneaccess` package:

- `gui.py`: the main window and the app start
- `dialogs.py`: Settings with its File name and Prompt profiles sub-dialogs, the start-chat dialog, and the message reader
- `chat_history.py`: the lines of the Chat history list and the usage line
- `openrouter.py`: requests to OpenRouter and the tool-call loop
- `tools.py`: the tools offered to the model and the code that runs them (ffmpeg frames, high-resolution sections)
- `prompts.py`: the base prompt and the default profiles
- `session_store.py`: chats in SQLite and the private video copies
- `youtube.py`: yt-dlp downloads, file names and video information
- `config.py`: settings in `config.json`
- `speech.py`: announcements through the running screen reader

`SceneAccess.py` is the launcher used by PyInstaller. Tests are in `tests/`, one file per module, plus checks of the documentation language, the default model and the release files.

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
