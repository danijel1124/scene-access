from types import SimpleNamespace

from sceneaccess.llm.client import Completion, Turn
from sceneaccess.pipeline import answering
from sceneaccess.pipeline.language import language_of, other_language
from sceneaccess.storage.session_store import OTHER_LANGUAGE
from sceneaccess.ui.chat_history import chat_history_rows

GERMAN = ("Eine junge Frau mit einem verkürzten Arm zeigt verschiedene Alltagsgegenstände. Sie hält eine Tube nah "
          "an die Kamera und öffnet mit dem Daumen den Deckel, der an der Tube bleibt.")
# What moonshotai/kimi-k3.1-pro sent through OpenRouter as its answer to a German question, 27.11.2026.
REASONING = ('lebte overall tone. Ensure no hearing-based "demonstriert" okay visible. "führt vor" visible. No '
             '"erklärt". Avoid retelling reasons. Fine.\n\nOne concern User just wants describe video, perhaps '
             "not overly verbose. Audio description with timestamps exactly requested by instructions. Good.\n\n"
             "I'll respond.")


def test_the_reasoning_sent_as_an_answer_is_found():
    assert other_language("Beschreibe das Video.", REASONING) == "English"


def test_an_answer_in_another_language_is_kept_and_says_so():
    turn = Turn(entries=[{"role": "assistant", "content": REASONING}],
                completion=Completion(REASONING, "m", 1, None, None, finish_reason="stop"))
    marked = answering.with_language(SimpleNamespace(question="Beschreibe das Video."), turn)
    # Kept as it is, only marked: it is paid for, and Qwen3.5-9B's English answers were often the more exact ones.
    assert marked.entries[-1]["content"] == REASONING and marked.entries[-1][OTHER_LANGUAGE] == "English"
    rows = chat_history_rows("Beschreibe das Video.", marked.entries, "none", "")
    assert any(shown.startswith("Note: The answer is in English") for shown, _full, _index, _kind in rows)


def test_an_answer_in_the_language_of_its_question_is_not_marked_also_with_some_other_words():
    assert other_language("Beschreibe das Video.",
                          GERMAN + ' Unten steht das Wasserzeichen „@gina.ruhl“ und "Made in the USA".') is None
    assert other_language("Describe the video.",
                          "A young woman shows a tube to the camera and opens its cap with her thumb.") is None


def test_quoted_text_does_not_count_and_a_question_naming_a_language_is_not_judged():
    assert other_language("Was steht genau auf der Packung?", "Auf der Packung steht: „Fresh breath all day. The "
                          "best for your teeth. With fluoride and no sugar.“") is None
    assert other_language("Schreib die Beschreibung bitte auf Englisch.", "A young woman shows a tube to the "
                          "camera and opens its cap with her thumb, and then she smiles.") is None


def test_nothing_is_judged_when_a_language_stays_unclear():
    # A question of one word, and an answer too short to tell.
    assert language_of("Video?", 1) is None
    assert other_language("Video?", "A young woman shows a tube to the camera and opens the cap.") is None
    assert other_language("Beschreibe das Video.", "Answer") is None
