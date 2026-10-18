from cards import load_default_registry
from game.enums import CardType


def test_base_pack_loads():
    reg = load_default_registry()
    s = reg.summary()
    assert s["packs_loaded"] == 1
    assert s["total_definitions"] == 20
    assert s["total_deck_copies"] == 85


def test_build_deck_has_unique_uids_and_matches_copies():
    deck = load_default_registry().build_deck()
    assert len(deck) == 85
    assert len({c.uid for c in deck}) == 85


def test_bang_definition_is_data_driven():
    d = load_default_registry().get_definition("bang")
    assert d.card_type == CardType.BROWN
    assert d.max_range == 1
    assert "attack" in d.tags
    assert d.effects[0].params["amount"] == 1
