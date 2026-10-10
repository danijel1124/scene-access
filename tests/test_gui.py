from sceneaccess.gui import cache_status_text


def test_cache_status_reports_actual_cached_tokens():
    assert cache_status_text(9137, 9175) == "Cache-Hit bestätigt: 9.137 von 9.175 Prompt-Tokens"


def test_cache_status_does_not_claim_hit_when_provider_reported_none():
    assert cache_status_text(0, 9175) == "Kein Cache-Hit bestätigt. Eine erneute Videoverarbeitung kann Kosten verursachen."
