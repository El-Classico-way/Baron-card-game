import pytest
from pydantic import ValidationError

from networking.protocol import mk_error, parse_inbound


def test_parse_play_card():
    t, p = parse_inbound({"type": "play_card",
                          "payload": {"card_uid": "bang-1", "target_id": "p2"}})
    assert t == "play_card"
    assert (p.card_uid, p.target_id) == ("bang-1", "p2")


def test_parse_unknown_type_raises_value_error():
    with pytest.raises(ValueError):
        parse_inbound({"type": "hack_the_planet", "payload": {}})


def test_parse_missing_required_field():
    with pytest.raises(ValidationError):
        parse_inbound({"type": "play_card", "payload": {}})


def test_draw_count_is_bounded():
    with pytest.raises(ValidationError):
        parse_inbound({"type": "draw_cards", "payload": {"count": 99}})


def test_error_envelope_shape():
    msg = mk_error("boom", "x")
    assert msg["type"] == "error"
    assert "boom" in str(msg["payload"])
