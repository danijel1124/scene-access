# SceneAccess

SceneAccess is a native accessibility tool for continuing conversations about local videos. It stores the video context locally, continues follow-up questions with a stable OpenRouter session ID, and provides a foundation for future audio description features.

[German translation](README.de.md)

## Usage

1. Open **Settings** and enter a model, base URL, and optionally an API key.
2. Choose **New video chat**, then **Start chat from local file** to select a local `.mp4`, `.webm`, or `.mov` file, or **Start chat from YouTube** to enter a link. A copied link is suggested automatically.
3. Enter the first question.
4. Ask follow-up questions in the question field. **Enter** sends the question, **Shift+Enter** inserts a line break.

The **Chat history** list contains one item per message. Below every answer, a usage line shows the model that actually answered (useful with `openrouter/auto`) and the prompt tokens read from the cache (hit), not served from the cache (miss), and written to the cache. **Enter** on a list item opens it in a read-only text field for reading line by line.

When a running screen reader such as NVDA is detected, SceneAccess announces that an answer is being generated, and moves the focus to the new answer when it arrives.

YouTube videos are downloaded with [yt-dlp](https://github.com/yt-dlp/yt-dlp) in a progress dialog that can be cancelled. By default SceneAccess downloads the best quality up to 480p, which keeps details recognizable without HD file sizes, with video and audio merged into an MP4. **Settings** lets you choose between 240p and 1080p. Videos are saved to `scene-access` in your Videos folder; if that folder does not exist, SceneAccess asks for a default download folder, which can also be changed in **Settings**. Files are named like yt-dlp's default, `Title [ID].mp4`; **Settings → File name…** lets you check any yt-dlp output template field to include, with further parts added in brackets. A bundled ffmpeg merges video and audio; if Deno or Node.js is installed, yt-dlp uses it for full YouTube support.

The sidebar lists recent chats. A chat remains usable if the original file is moved or deleted because the private local video context is retained.

## Configuration and privacy

SceneAccess uses `wx.StandardPaths.GetUserDataDir()` and therefore the native per-user application-data directory of the operating system. No Linux, Windows, or Hermes paths are hard-coded.

That directory contains:

- `config.json`: model, base URL, and optionally the API key
- `chats/sceneaccess.db`: titles, models, paths, and conversation histories
- `chats/sessions/<id>/context.data-url`: a complete reconstructable video copy as a Base64 data URL

Alternatively, set `OPENROUTER_API_KEY`; the environment variable takes precedence. Base64 is not encryption. Anyone who can access `context.data-url` has a copy of the video.

## Run from source

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -p no:cacheprovider tests/ -q
python SceneAccess.py
```

## Windows package through GitHub Actions

The workflow in `.github/workflows/windows-release.yml` runs on a real Windows runner. It tests the source, builds `SceneAccess.exe` with PyInstaller in `--onedir` mode, and uploads a `SceneAccess-windows-x64.zip` artifact.

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
