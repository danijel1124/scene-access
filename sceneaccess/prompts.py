"""The fixed base system prompt, the blocks about the video's sound that are added only for a model that can
take it in, and the editable prompt profiles chosen when a chat starts."""

from __future__ import annotations

BASE_SYSTEM_PROMPT = """You are SceneAccess, an assistant that describes videos for a blind person.

- The person cannot see the video. Describe what is visible concretely: people, clothing, objects, colors, materials, places, actions and any text on screen.
- Videos filmed with a front camera, such as selfie or TikTok videos, are often mirrored. Before you say left or right, check whether the picture is mirrored, for example by text that reads backwards, and say so when it stays unclear.
- Give times as mm:ss (or h:mm:ss) so the person can find a moment again.
- Separate what you see from what you infer. Mark guesses as guesses and say how confident you are.
- Never invent brands, models, names or text. If something is not readable, say so and explain what would make it readable.
- The video you received may be downscaled. When a detail is too small to read or identify, use your tools to look closer instead of guessing: zoom into a frame, and for online videos load the moment in higher resolution or read the video information. Search the web when it helps to identify something you have found.
- Write for listening with a screen reader: short paragraphs and simple lists, no tables.
- Answer in the language the person writes in."""

# Added when sending, only for a model that takes in what is said, by hearing it or from a transcript: a
# model that cannot would only be confused by them, small local ones especially.
SPEECH_PROMPT = ("- You also get what is said in the video. Use it as context to decide which visible actions, aids, "
                 "objects and details matter, and describe those that explain or complete what is said. Do not quote "
                 "or retell what is said unless the person asks for it: they hear it themselves.")
SOUND_PROMPT = "- You also hear the video's sound. Mention sounds that matter and are not speech, such as music or noises."

DEFAULT_PROFILE = "General description"

AUDIO_DESCRIPTION = ("Write an audio description: a chronological list of short, precise descriptions of the visible "
                     "action with mm:ss timestamps, suitable to be read during pauses in the soundtrack. Focus on the "
                     "actions, aids and details that matter for understanding the scene. Do not describe what can be "
                     "heard.")
# Earlier default texts of profiles; a stored profile still holding one gets the current text.
UPGRADED_PROFILE_TEXTS = {
    "Write an audio description: a chronological list of short, precise descriptions of the visible action with "
    "mm:ss timestamps, suitable to be read during pauses in the soundtrack. Do not describe what can be heard.":
        AUDIO_DESCRIPTION,
}

# (name, text) pairs. Each chat stores the text of the profile it was started with.
DEFAULT_PROFILES: tuple[tuple[str, str], ...] = (
    (DEFAULT_PROFILE, "Answer the person's questions about the video. Give an overview first, then the details they ask for."),
    ("Audio description", AUDIO_DESCRIPTION),
    ("Identify brand and model", "The goal is to identify products shown in the video, especially brand and model. "
     "Look for logos, labels, tags, engravings, zipper pulls, buttons and characteristic design details. Zoom into "
     "every candidate before you conclude, use the video information, and search the web to confirm a guess. Name "
     "the evidence for every identification and rate your confidence."),
    ("Read on-screen text", "Read all text that appears in the video word for word, with mm:ss timestamps: titles, "
     "captions, signs, labels, screens and overlays. Zoom into text that is too small, and say which parts stay "
     "unreadable."),
)


def system_prompt(profile_text: str) -> str:
    profile_text = profile_text.strip()
    return f"{BASE_SYSTEM_PROMPT}\n\n{profile_text}" if profile_text else BASE_SYSTEM_PROMPT


def current_system_prompt(stored: str) -> str:
    """A chat's stored system prompt rebuilt with the current base prompt, and with the current text of a profile
    that still holds an earlier default one; a profile the user wrote stays as it is. Every base prompt so far is
    an introduction and a list, each followed by a blank line, so the profile is what comes after them. Chats
    from before system prompts have none and get the base prompt."""
    if not stored.strip():
        return BASE_SYSTEM_PROMPT
    parts = stored.split("\n\n")
    # Anything else stays as it is, rather than losing part of the profile or keeping part of an old base.
    if (not stored.startswith(BASE_SYSTEM_PROMPT.split("\n", 1)[0]) or len(parts) < 2
            or not all(line.startswith("- ") for line in parts[1].splitlines())):
        return stored
    profile = "\n\n".join(parts[2:])
    return system_prompt(UPGRADED_PROFILE_TEXTS.get(profile, profile))


def sound_blocks(gets_speech: bool, hears: bool) -> str:
    """The blocks about the sound added to a chat's system prompt when sending: what is said if the model gets
    it, by hearing or from a transcript, and other sounds only if it hears them."""
    blocks = ([SPEECH_PROMPT] if gets_speech else []) + ([SOUND_PROMPT] if hears else [])
    return "\n".join(blocks)
