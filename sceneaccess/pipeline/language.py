"""Whether an answer came in the language of its question. Models are asked to answer in it; one that does not may
have sent its reasoning as the answer, as moonshotai/kimi-k3.1-pro did through OpenRouter (27.11.2026): "lebte
overall tone. Ensure no hearing-based ... I'll respond." instead of a German description. Such an answer is kept and
shown, as it is paid for and Qwen3.5-9B's English answers were often the more exact ones (user's view), but it says
so. Told apart by their most common short words, which needs no package; when either language stays unclear, e.g.
for a very short question, nothing is judged."""

from __future__ import annotations

import re

_WORDS = {
    "German": "der die das und ist nicht ein eine einen einem einer sie er es ich wir ihr mit auf für von zu im den "
              "dem des sich auch was wie wo wer warum steht hält zeigt beschreibe beschreib bitte ihn ihre nein "
              "durch aber sein wenn oder als an wird soll kann sind",
    "English": "the and is are not a an she he it i we you with on for of to in that this what how where who why "
               "her his its there describe please no by but be if so or as at will should can",
    "French": "le la les et est pas un une elle il je nous vous avec sur pour de du des dans que qui quoi comment "
              "mais ou si par sont",
    "Spanish": "el la los las y es no un una ella él yo con en para de del que qué cómo dónde por pero o si son",
    "Italian": "il lo la gli le e è non un una lei lui io con su per di del che cosa come dove ma o se sono",
    "Dutch": "de het een en is niet zij hij ik wij met op voor van te in dat wat hoe waar wie maar of als zijn",
}
_SETS = {language: set(words.split()) for language, words in _WORDS.items()}
# Quoted text, such as what a package says, is in its own language and not counted.
_QUOTED = re.compile(r"„[^“”]*[“”]|\"[^\"]*\"|“[^”]*”|»[^«]*«|«[^»]*»")
# A question naming a language may ask for an answer in it ("Schreib es auf Englisch"): then nothing is judged.
_LANGUAGE_NAMES = re.compile(r"(?i)\b(english|englisch|german|deutsch|french|französisch|français|spanish|spanisch|"
                             r"español|italian|italienisch|italiano|dutch|niederländisch|nederlands)\b")
_WORD = re.compile(r"[^\W\d_]+")


def language_of(text: str, at_least: int) -> str | None:
    """The language most of text's common words outside quotes belong to, if at least that many do and clearly more
    than of any other; else None."""
    words = [word.casefold() for word in _WORD.findall(_QUOTED.sub(" ", text))]
    counts = sorted(((sum(word in found for word in words), language) for language, found in _SETS.items()),
                    reverse=True)
    (best, language), second = counts[0], counts[1][0]
    return language if best >= at_least and best >= 2 * second + 1 else None


def other_language(question: str, answer: str) -> str | None:
    """The language answer is in, when it is clearly another one than question's; else None. A question has few
    words, so one common word decides it; an answer needs a few. A question naming a language is not judged."""
    if _LANGUAGE_NAMES.search(question):
        return None
    asked, answered = language_of(question, 1), language_of(answer, 5)
    return answered if asked and answered and asked != answered else None


def other_language_note(language: str) -> str:
    """What the Chat history and the status line say under an answer in another language than its question."""
    return (f"The answer is in {language}, not in the language of the question. If it reads like the model's "
            "reasoning rather than an answer, send the question again.")
