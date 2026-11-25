"""End-to-end check: a llama.cpp chat sent through SceneAccess's own pipeline, as the app sends it.
Usage: python tools\\e2e_llama.py [server URL]; without a URL, the one from the SceneAccess settings is used."""

import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imageio_ffmpeg  # noqa: E402
import subprocess  # noqa: E402

from sceneaccess import answering  # noqa: E402
from sceneaccess.config import AppConfig  # noqa: E402
from sceneaccess.models import ModelCatalog  # noqa: E402
from sceneaccess.prompts import system_prompt  # noqa: E402
from sceneaccess.providers import LLAMA_CPP, OPENROUTER_URL  # noqa: E402
from sceneaccess.session_store import VideoSessionStore  # noqa: E402
from sceneaccess.tools import tool_definitions  # noqa: E402

if len(sys.argv) > 1:
    URL = sys.argv[1]
else:
    config = Path(os.environ["APPDATA"]) / "SceneAccess" / "config.json"
    URL = json.loads(config.read_text(encoding="utf-8"))["llama_cpp_url"]
work = Path(tempfile.mkdtemp(prefix="sceneaccess-e2e-"))
clip = work / "testclip.mp4"
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i",
                "testsrc=duration=10:size=640x360:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(clip)], check=True)

session = VideoSessionStore(work / "chats").create_session(
    owner_id="e2e", source_path=clip, title="testclip.mp4", model="", provider=LLAMA_CPP,
    initial_question="Beschreibe kurz, was in diesem Video zu sehen ist. Nutze das Zoom-Werkzeug einmal "
                     "für den Zähler in der Bildmitte bei 00:05.",
    system_prompt=system_prompt(""), tools=tool_definitions(online_video=False, web_search=False))
session = replace(session, model="default")
events = []
started = time.monotonic()
# Without an OpenRouter key: nothing of this check is billed.
request = answering.prepare(AppConfig(llama_cpp_url=URL), ModelCatalog(OPENROUTER_URL), session,
                            session.initial_question, initial=True, openrouter_key=None)
turn, runner = answering.answer(request, on_event=lambda kind, text: events.append(f"{kind}: {text}"))
if runner is not None:
    runner.cleanup_files()
print(f"Dauer: {time.monotonic() - started:.1f} s")
for event in events:
    print("Ereignis:", event)
completion = turn.completion
print("Modell:", completion.model, "| Anfragen:", completion.requests, "| Prompt-Tokens:", completion.prompt_tokens,
      "| Werkzeuge verworfen:", completion.tools_unavailable)
print("Antwort:", completion.answer)
