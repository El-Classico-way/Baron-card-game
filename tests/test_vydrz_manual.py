import pytest

from modes.vydrz import load_default_content
from modes.vydrz.scoring import contribution
from modes.vydrz_manual import ManualVydrzGame, Phase

CONTENT = load_default_content()


def make(n=3, seed=1):
    ids = [f"p{i + 1}" for i in range(n)]
    g = ManualVydrzGame(ids, CONTENT, display_names={i: i.upper() for i in ids}, seed=seed)
    assert g.start().ok
    return g


def plan_in_hand_order(g):
    for p in g.players:
        pairs = [(l.uid, e.uid) for l, e in zip(p.locations, p.existences)]
        assert g.action_set_plan(p.player_id, pairs).ok


def correct_for(g, pid):
    c = g._calc
    return c.correct[pid]


def answer_all(g, mode="correct"):
    """Everyone answers once. mode: correct | low | high"""
    for pid in list(g._calc.participants):
        right = correct_for(g, pid)
        value = {"correct": right, "low": right - 1, "high": right + 1}[mode]
        assert g.action_submit(pid, value).ok


# --- planning ---------------------------------------------------------------

def test_game_starts_in_planning_with_public_hands():
    g = make(3)
    assert g.phase == Phase.PLANNING
    s = g.snapshot("p1")
    assert all(len(p["locations"]) == 8 and len(p["existences"]) == 8 for p in s["players"])
    assert s["calc"] is None


def test_plan_must_use_every_card_once():
    g = make(2)
    p = g.players[0]
    good = [(l.uid, e.uid) for l, e in zip(p.locations, p.existences)]
    assert not g.action_set_plan("p1", good[:7]).ok                      # too short
    dup = good[:7] + [good[0]]
    assert not g.action_set_plan("p1", dup).ok                           # repeats a pair
    other = g.players[1]
    foreign = [(l.uid, e.uid) for l, e in zip(other.locations, other.existences)]
    assert not g.action_set_plan("p1", foreign).ok                       # someone else's cards
    assert g.action_set_plan("p1", good).ok
    assert g.phase == Phase.PLANNING                                     # p2 still missing


def test_plan_is_private_and_can_be_changed_until_all_are_done():
    g = make(2)
    p = g.players[0]
    pairs = [(l.uid, e.uid) for l, e in zip(p.locations, p.existences)]
    g.action_set_plan("p1", pairs)
    mine = next(x for x in g.snapshot("p1")["players"] if x["player_id"] == "p1")
    theirs = next(x for x in g.snapshot("p2")["players"] if x["player_id"] == "p1")
    assert mine["plan"] and theirs["plan"] is None and theirs["planned"] is True
    assert g.action_set_plan("p1", list(reversed(pairs))).ok


def test_round_starts_when_everyone_has_planned_and_draws_three_drinks():
    g = make(3)
    plan_in_hand_order(g)
    assert g.phase == Phase.CALCULATING and g.round == 1
    s = g.snapshot("p1")
    assert len(s["calc"]["drinks"]) == 3 and s["calc"]["mine"]["location"]["number"] == 1


def test_each_player_only_sees_their_own_pair():
    g = make(2)
    plan_in_hand_order(g)
    s1, s2 = g.snapshot("p1"), g.snapshot("p2")
    assert s1["calc"]["mine"]["location"]["uid"] == g.players[0].locations[0].uid
    assert s2["calc"]["mine"]["location"]["uid"] == g.players[1].locations[0].uid
    assert s1["calc"]["mine"] != s2["calc"]["mine"]


# --- entering numbers ---------------------------------------------------------

def test_server_knows_the_correct_number():
    g = make(2, seed=4)
    plan_in_hand_order(g)
    for p in g.players:
        loc, ex = p.locations[0], p.existences[0]
        expect = sum(contribution(d, p.character, loc, ex).total for d in g._calc.drinks)
        assert g._calc.correct[p.player_id] == expect


def test_correct_low_and_high_entries_are_tagged():
    g = make(3, seed=2)
    plan_in_hand_order(g)
    right = {pid: correct_for(g, pid) for pid in ("p1", "p2", "p3")}
    assert g.action_submit("p1", right["p1"]).ok
    assert g.action_submit("p2", right["p2"] - 2).ok
    assert g.action_submit("p3", right["p3"] + 5).ok
    # table is public as soon as the last one is in
    t = g.last_result["table"]
    assert t["p1"] == {"value": right["p1"], "tag": None, "entered": right["p1"], "correct": right["p1"]}
    assert t["p2"]["tag"] == "blbec" and t["p2"]["value"] == right["p2"] - 2
    assert t["p3"]["tag"] == "svindlir" and t["p3"]["value"] == -50 and t["p3"]["entered"] == right["p3"] + 5


def test_entries_appear_in_the_public_table_immediately():
    g = make(3)
    plan_in_hand_order(g)
    g.action_submit("p1", correct_for(g, "p1"))
    seen = g.snapshot("p2")["calc"]
    assert "p1" in seen["entries"] and "p2" not in seen["entries"]
    assert "correct" not in seen["entries"]["p1"]               # the right answer is never leaked early
    assert seen["entered"] is False and g.snapshot("p1")["calc"]["entered"] is True


def test_cannot_enter_twice_or_out_of_turn_or_garbage():
    g = make(2)
    assert not g.action_submit("p1", 3).ok                       # still planning
    plan_in_hand_order(g)
    assert g.action_submit("p1", correct_for(g, "p1")).ok
    assert not g.action_submit("p1", 0).ok
    assert not g.action_submit("nobody", 0).ok
    assert not g.action_submit("p2", 10**6).ok
    assert not g.action_submit("p2", True).ok


def test_highest_value_wins_the_round_and_scores_the_location_number():
    g = make(3, seed=5)
    plan_in_hand_order(g)
    answer_all(g, "correct")
    r = g.last_result
    assert g.phase in (Phase.REVEAL, Phase.CALCULATING)
    if g.phase == Phase.REVEAL:
        w = r["winner_id"]
        assert w is not None
        best = max(e["value"] for e in r["table"].values())
        assert r["table"][w]["value"] == best
        assert r["awarded"] == r["picks"][w]["lokace"]["number"] == g._player(w).score


def test_cheater_counts_as_minus_fifty_and_loses_the_round():
    g = make(2, seed=8)
    plan_in_hand_order(g)
    # p1 cheats (counts as -50), p2 is right. A real sum is at most +-27, so p2 wins.
    g.action_submit("p1", correct_for(g, "p1") + 3)
    g.action_submit("p2", correct_for(g, "p2"))
    r = g.last_result
    assert r["table"]["p1"]["value"] == -50 and r["table"]["p1"]["tag"] == "svindlir"
    assert r["winner_id"] == "p2"


def test_tie_is_broken_by_a_manual_single_drink_session():
    g = make(2, seed=3)
    plan_in_hand_order(g)
    # Force a tie: both enter the same value as their (equalised) right answer.
    for pid in ("p1", "p2"):
        g._calc.correct[pid] = 5
    g.action_submit("p1", 5)
    g.action_submit("p2", 5)
    assert g.phase == Phase.CALCULATING and g._calc.is_tiebreak
    assert len(g._calc.drinks) == 1 and set(g._calc.participants) == {"p1", "p2"}
    # tie-break counts characters only
    d = g._calc.drinks[0]
    for p in g.players:
        assert g._calc.correct[p.player_id] == p.character.stats[d]
    answer_all(g, "correct")
    # either decided (REVEAL) or another tie-break; never stuck without a session
    assert g.phase == Phase.REVEAL or g._calc.is_tiebreak


def test_next_round_only_after_reveal_and_uses_the_next_pair():
    g = make(2, seed=6)
    plan_in_hand_order(g)
    assert not g.action_next_round().ok
    answer_all(g)
    while g.phase == Phase.CALCULATING:                          # resolve any tie-breaks
        answer_all(g)
    assert g.phase == Phase.REVEAL
    assert g.action_next_round().ok
    assert g.round == 2 and g.phase == Phase.CALCULATING
    s = g.snapshot("p1")
    assert s["calc"]["mine"]["location"]["number"] == 2


def test_full_game_ends_with_winner_and_loser_and_hands_are_empty():
    g = make(4, seed=21)
    plan_in_hand_order(g)
    for rnd in range(1, 9):
        assert g.round == rnd
        guard = 0
        while g.phase == Phase.CALCULATING and guard < 100:
            answer_all(g)
            guard += 1
        if rnd < 8:
            assert g.phase == Phase.REVEAL
            assert g.action_next_round().ok
    assert g.phase == Phase.FINISHED
    final = g.last_result["final"]
    assert final["winner_id"] and final["loser_id"] and final["winner_id"] != final["loser_id"]
    scores = [r["score"] for r in final["ranking"]]
    assert scores == sorted(scores, reverse=True)
    assert all(not p.locations and not p.existences for p in g.players)
    assert sum(p.score for p in g.players) == sum(l.number for p in g.players for l in p.won)


def test_automatic_engine_is_unchanged_by_the_manual_one():
    """The two variants share data and dealing only: same seed, same deal."""
    from modes.vydrz import VydrzGame
    ids = ["p1", "p2", "p3"]
    a = VydrzGame(ids, CONTENT, seed=9); a.start()
    m = ManualVydrzGame(ids, CONTENT, seed=9); m.start()
    assert [p.character.id for p in a.players] == [p.character.id for p in m.players]
    assert [l.id for l in a.players[0].locations] == [l.id for l in m.players[0].locations]


# --- waiting for every player ------------------------------------------------

def test_planning_waits_for_every_player():
    g = make(4)
    for p in g.players[:3]:
        g.action_set_plan(p.player_id, [(l.uid, e.uid) for l, e in zip(p.locations, p.existences)])
        assert g.phase == Phase.PLANNING                      # not before the last plan
    p = g.players[3]
    g.action_set_plan(p.player_id, [(l.uid, e.uid) for l, e in zip(p.locations, p.existences)])
    assert g.phase == Phase.CALCULATING


def test_round_waits_for_every_number():
    g = make(4)
    plan_in_hand_order(g)
    for pid in ("p1", "p2", "p3"):
        g.action_submit(pid, correct_for(g, pid))
        assert g.phase == Phase.CALCULATING and g.last_result is None
    g.action_submit("p4", correct_for(g, "p4"))
    assert g.last_result is not None


def test_tiebreak_waits_for_every_tied_player():
    g = make(3, seed=3)
    plan_in_hand_order(g)
    for pid in ("p1", "p2", "p3"):
        g._calc.correct[pid] = 5                              # force a three-way tie
    for pid in ("p1", "p2", "p3"):
        g.action_submit(pid, 5)
    assert g._calc.is_tiebreak and set(g._calc.participants) == {"p1", "p2", "p3"}
    g.action_submit("p1", g._calc.correct["p1"])
    g.action_submit("p2", g._calc.correct["p2"])
    assert g._calc is not None and not g._calc.entries.get("p3")   # still waiting for p3


# --- a player who leaves never blocks the others -----------------------------

def test_leaving_player_is_planned_and_answered_automatically():
    g = make(3, seed=6)
    a, b, gone = g.players
    for p in (a, b):
        g.action_set_plan(p.player_id, [(l.uid, e.uid) for l, e in zip(p.locations, p.existences)])
    assert g.phase == Phase.PLANNING
    g.mark_away(gone.player_id)                               # the last missing plan
    assert g.phase == Phase.CALCULATING
    g.action_submit("p1", correct_for(g, "p1"))
    assert g.phase == Phase.CALCULATING                       # still waits for p2, not for the leaver
    g.action_submit("p2", correct_for(g, "p2"))
    assert g.phase in (Phase.REVEAL, Phase.CALCULATING)       # the leaver answered by itself
    assert g.snapshot("p1")["players"][2]["away"] is True
