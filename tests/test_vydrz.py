import pytest

from modes.vydrz import Phase, VydrzGame, load_default_content
from modes.vydrz.loader import ContentError, parse_content
from modes.vydrz.models import DRINKS

CONTENT = load_default_content()


def make(n=3, seed=1):
    ids = [f"p{i + 1}" for i in range(n)]
    g = VydrzGame(ids, CONTENT, display_names={i: i.upper() for i in ids}, seed=seed)
    assert g.start().ok
    return g


def choose_first(g, offset=0):
    for p in g.players:
        assert g.action_choose(p.player_id, p.locations[offset % len(p.locations)].uid,
                               p.existences[offset % len(p.existences)].uid).ok


# --- content ---------------------------------------------------------------

def test_default_content_is_valid_and_complete():
    assert len(CONTENT.characters) == 10
    assert len(CONTENT.make_drinks()) == 100
    assert all(2 <= len(l.stats) <= 3 for l in CONTENT.make_locations())
    assert {l.number for l in CONTENT.make_locations()} == set(range(1, 9))
    assert all(set(c.stats) == set(DRINKS) for c in CONTENT.make_characters())


def test_bad_content_is_rejected():
    data = {"drinks": [{"id": d, "name": d} for d in DRINKS], "characters": [], "existences": [],
            "locations": [{"id": "x", "number": 9, "name": "x", "stats": {"pivo": 1, "gin": 1}}]}
    with pytest.raises(ContentError):
        parse_content(data)


# --- setup -----------------------------------------------------------------

def test_deal_gives_each_player_one_of_each_number_and_eight_existences():
    g = make(4)
    assert g.phase == Phase.CHOOSING and g.round == 1
    for p in g.players:
        assert sorted(l.number for l in p.locations) == list(range(1, 9))
        assert len(p.existences) == 8
    all_locs = [l.uid for p in g.players for l in p.locations]
    assert len(all_locs) == len(set(all_locs))               # nobody shares a card
    assert len({p.character.uid for p in g.players}) == 4


@pytest.mark.parametrize("n", [1, 8])
def test_player_count_limits(n):
    g = VydrzGame([f"p{i}" for i in range(n)], CONTENT, seed=1)
    assert not g.start().ok


def test_same_seed_same_deal():
    a, b = make(3, seed=5), make(3, seed=5)
    assert [p.character.id for p in a.players] == [p.character.id for p in b.players]
    assert [l.id for l in a.players[0].locations] == [l.id for l in b.players[0].locations]


# --- choosing --------------------------------------------------------------

def test_choose_validates_cards_and_phase():
    g = make(2)
    p1, p2 = g.players
    assert not g.action_choose("p1", p2.locations[0].uid, p1.existences[0].uid).ok   # not yours
    assert not g.action_choose("p1", p1.locations[0].uid, p2.existences[0].uid).ok
    assert not g.action_choose("nobody", "x", "y").ok
    assert g.action_choose("p1", p1.locations[0].uid, p1.existences[0].uid).ok
    assert g.phase == Phase.CHOOSING                      # waiting for p2


def test_picks_are_hidden_from_others_until_resolved():
    g = make(3)
    p1 = g.players[0]
    g.action_choose("p1", p1.locations[0].uid, p1.existences[0].uid)
    seen_by_p2 = next(p for p in g.snapshot("p2")["players"] if p["player_id"] == "p1")
    assert seen_by_p2["locked"] is True and seen_by_p2["pick"] is None
    seen_by_p1 = next(p for p in g.snapshot("p1")["players"] if p["player_id"] == "p1")
    assert seen_by_p1["pick"] is not None


def test_pick_can_be_changed_until_everyone_has_chosen():
    g = make(2)
    p1 = g.players[0]
    g.action_choose("p1", p1.locations[0].uid, p1.existences[0].uid)
    assert g.action_choose("p1", p1.locations[1].uid, p1.existences[1].uid).ok
    assert g.players[0].pick == (p1.locations[1].uid, p1.existences[1].uid)


# --- scoring ---------------------------------------------------------------

def test_round_resolution_matches_manual_sum():
    g = make(3, seed=7)
    picks = {p.player_id: (p.locations[0], p.existences[0]) for p in g.players}
    for p in g.players:
        g.action_choose(p.player_id, picks[p.player_id][0].uid, picks[p.player_id][1].uid)
    r = g.last_result
    assert g.phase == Phase.REVEAL and len(r["drawn"]) == 3
    for pid in picks:
        expect = 0
        for row in r["drawn"]:
            d = row["drink"]
            p = g._player(pid)
            expect += (p.character.stats[d]
                       + picks[pid][0].stats.get(d, 0) + picks[pid][1].stats.get(d, 0))
        assert r["totals"][pid] == expect


def test_winner_keeps_location_and_scores_its_number_losers_discard():
    g = make(3, seed=11)
    choose_first(g)
    r = g.last_result
    w = r["winner_id"]
    assert w is not None
    wp = g._player(w)
    assert r["awarded"] == r["picks"][w]["lokace"]["number"] == wp.score
    assert [l.uid for l in wp.won] == [r["picks"][w]["lokace"]["uid"]]
    for p in g.players:
        assert len(p.locations) == 7 and len(p.existences) == 7       # played cards left hand
        if p.player_id != w:
            assert p.won == [] and p.score == 0


def test_tie_is_broken_by_character_stats_only():
    g = make(2, seed=3)
    # Force an exact tie: identical characters and no location/existence stats matter
    p1, p2 = g.players
    object.__setattr__(p1.character, "stats", dict(p2.character.stats))
    choose_first(g)
    r = g.last_result
    if r["totals"]["p1"] == r["totals"]["p2"]:
        assert r["tiebreak"], "a tie must trigger a tie-break draw"
        for step in r["tiebreak"]:
            assert set(step["values"]) <= {"p1", "p2"}
    # every tie-break step narrows to the leaders of its own values
    for step in r["tiebreak"]:
        best = max(step["values"].values())
        assert all(step["values"][pid] == best for pid in step["remaining"])


def test_full_game_ends_after_eight_rounds_with_winner_and_loser():
    g = make(4, seed=21)
    for rnd in range(1, 9):
        assert g.round == rnd and g.phase == Phase.CHOOSING
        choose_first(g)
        if rnd < 8:
            assert g.phase == Phase.REVEAL
            assert g.action_next_round().ok
    assert g.phase == Phase.FINISHED
    final = g.last_result["final"]
    assert final["winner_id"] and final["loser_id"] and final["winner_id"] != final["loser_id"]
    scores = [r["score"] for r in final["ranking"]]
    assert scores == sorted(scores, reverse=True)
    assert final["ranking"][0]["player_id"] == final["winner_id"]
    assert final["ranking"][-1]["player_id"] == final["loser_id"]
    assert all(len(p.locations) == 0 and len(p.existences) == 0 for p in g.players)
    assert sum(p.score for p in g.players) == sum(l.number for p in g.players for l in p.won)


def test_next_round_only_after_reveal():
    g = make(2)
    assert not g.action_next_round().ok


def test_drink_deck_reshuffles_when_empty():
    g = make(2, seed=9)
    g._drink_discard, g._drink_deck = g._drink_deck, []      # exhaust the draw pile
    assert g._draw_drink().drink in DRINKS


def test_snapshot_shows_public_hands_and_deck_counts():
    g = make(3)
    s = g.snapshot("p1")
    assert s["mode"] == "vydrz" and s["round"] == 1 and s["you"] == "p1"
    assert all(len(p["locations"]) == 8 and len(p["existences"]) == 8 for p in s["players"])
    assert s["decks"]["chlast"] == 100 and s["decks"]["postava"] == 7


# --- waiting for every player ------------------------------------------------

def test_round_waits_for_every_player_however_many_have_picked():
    g = make(4)
    for i, p in enumerate(g.players[:3]):
        assert g.action_choose(p.player_id, p.locations[0].uid, p.existences[0].uid).ok
        assert g.phase == Phase.CHOOSING and g.last_result is None, f"resolved early after {i + 1} of 4"
    last = g.players[3]
    assert g.action_choose(last.player_id, last.locations[0].uid, last.existences[0].uid).ok
    assert g.phase == Phase.REVEAL                                # only the last pick resolves the round


def test_next_round_does_not_start_by_itself():
    g = make(3)
    choose_first(g)
    assert g.phase == Phase.REVEAL and g.round == 1               # waits for the host's "next round"


# --- a player who leaves never blocks the others -----------------------------

def test_leaving_player_is_played_automatically():
    g = make(3, seed=2)
    a, b, gone = g.players
    g.action_choose(a.player_id, a.locations[0].uid, a.existences[0].uid)
    g.mark_away(gone.player_id)
    assert g.phase == Phase.CHOOSING                              # still waiting for b
    g.action_choose(b.player_id, b.locations[0].uid, b.existences[0].uid)
    assert g.phase == Phase.REVEAL
    assert g.snapshot("p1")["players"][2]["away"] is True
    g.action_next_round()
    assert g.phase == Phase.CHOOSING                              # the leaver is auto-picked again next round
    assert gone.pick is not None and a.pick is None


def test_game_finishes_even_if_everyone_but_one_leaves():
    g = make(3, seed=4)
    g.mark_away("p2"); g.mark_away("p3")
    for _ in range(8):
        p = g.players[0]
        g.action_choose("p1", p.locations[0].uid, p.existences[0].uid)
        if g.phase == Phase.REVEAL:
            g.action_next_round()
    assert g.phase == Phase.FINISHED
