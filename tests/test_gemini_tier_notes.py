from app.providers.gemini import parse_tier_notes


def test_parse_tier_notes_valid_json():
    raw = '{"economical": "repaint over stains", "mid": "replace flooring", "premium": "add trim"}'
    result = parse_tier_notes(raw)
    assert result == {
        "economical": "repaint over stains",
        "mid": "replace flooring",
        "premium": "add trim",
    }


def test_parse_tier_notes_strips_markdown_fences():
    raw = '```json\n{"economical": "repaint over stains"}\n```'
    result = parse_tier_notes(raw)
    assert result == {"economical": "repaint over stains"}


def test_parse_tier_notes_strips_plain_fences_no_language_tag():
    raw = '```\n{"mid": "replace flooring"}\n```'
    result = parse_tier_notes(raw)
    assert result == {"mid": "replace flooring"}


def test_parse_tier_notes_invalid_json_returns_empty_dict():
    result = parse_tier_notes("not json at all")
    assert result == {}


def test_parse_tier_notes_ignores_unknown_keys_and_non_string_values():
    raw = '{"economical": "ok note", "unknown_tier": "ignored", "mid": 123}'
    result = parse_tier_notes(raw)
    assert result == {"economical": "ok note"}


def test_parse_tier_notes_non_dict_json_returns_empty_dict():
    result = parse_tier_notes("[1, 2, 3]")
    assert result == {}
