import pytest

from game.enums import GamePhase, PlayerStatus, Role, TurnPhase


def test_start_assigns_roles_hp_and_hands(make_game):
    h = make_game(4)
    assert h.state.phase == GamePhase.IN_PROGRESS
    roles = sorted(p.role.value for p in h.state.players)
    assert roles == sorted(r.value for r in
                           [Role.SHERIFF, Role.OUTLAW, Role.OUTLAW, Role.RENEGADE])
    for p in h.state.players:
        assert p.hp == (5 if p.role == Role.SHERIFF else 4)
        assert p.status == PlayerStatus.ALIVE
        assert p.hand_count() > 0


def test_sheriff_goes_first(make_game):
    h = make_game(5)
    assert h.current.role == Role.SHERIFF
    assert h.state.turn.phase == TurnPhase.DRAW


@pytest.mark.parametrize("n", [1, 8])
def test_start_rejects_bad_player_count(make_game, n):
    h = make_game(n, start=False)
    assert not h.start().ok


def test_cannot_start_twice(make_game):
    h = make_game(4)
    assert not h.start().ok


def test_same_seed_is_deterministic(make_game):
    a, b = make_game(4, seed=7), make_game(4, seed=7)
    assert [p.role for p in a.state.players] == [p.role for p in b.state.players]
    # uids are random per card instance; compare what the cards actually are.
    def hands(h):
        return [[(c.id, c.suit, c.rank) for c in p.hand] for p in h.state.players]
    assert hands(a) == hands(b)


def test_draw_moves_to_play_phase(make_game):
    h = make_game(4)
    p = h.current
    before = p.hand_count()
    assert h.game.action_draw_cards(p.player_id).ok
    assert p.hand_count() == before + 2
    assert h.state.turn.phase == TurnPhase.PLAY


def test_wrong_player_and_wrong_phase_rejected(make_game):
    h = make_game(4)
    other = next(x for x in h.ids if x != h.current.player_id)
    assert not h.game.action_draw_cards(other).ok
    h.game.action_draw_cards(h.current.player_id)
    assert not h.game.action_draw_cards(h.current.player_id).ok  # already drew


def test_end_turn_advances_to_next_player(make_game):
    h = make_game(4)
    first = h.current
    h.game.action_draw_cards(first.player_id)
    # Trim to hand limit so ending is legal.
    while first.hand_count() > first.hp:
        first.hand.pop()
    assert h.game.action_end_turn(first.player_id).ok
    assert h.current.player_id != first.player_id
    assert h.state.turn.phase == TurnPhase.DRAW
    assert len(h.events_of("TurnStartedEvent")) == 2


def test_cannot_end_turn_out_of_turn(make_game):
    h = make_game(4)
    other = next(x for x in h.ids if x != h.current.player_id)
    assert not h.game.action_end_turn(other).ok


def _give(h, player, card_id):
    """Move a card of the given definition id from the deck into player's hand."""
    deck = h.state.deck
    card = next(c for c in deck._draw_pile if c.id == card_id) \
        if hasattr(deck, "_draw_pile") else None
    assert card is not None, "deck internals changed; update helper"
    deck._draw_pile.remove(card)
    player.deal(card)
    return card


def _ready_to_play(h):
    p = h.current
    h.game.action_draw_cards(p.player_id)
    return p


def test_bang_requires_target(make_game):
    h = make_game(4)
    p = _ready_to_play(h)
    bang = _give(h, p, "bang")
    assert not h.game.action_play_card(p.player_id, bang.uid).ok


def test_cannot_play_card_not_in_hand(make_game):
    h = make_game(4)
    p = _ready_to_play(h)
    assert not h.game.action_play_card(p.player_id, "nope", None).ok


def test_bang_damages_adjacent_target(make_game):
    h = make_game(4)
    p = _ready_to_play(h)
    bang = _give(h, p, "bang")
    target = h.state.next_alive_player(p.player_id)
    hp = target.hp
    res = h.game.action_play_card(p.player_id, bang.uid, target.player_id)
    assert res.ok, res.error
    assert target.hp == hp - 1
    assert h.events_of("DamageDealtEvent")


def test_only_one_bang_per_turn(make_game):
    h = make_game(4)
    p = _ready_to_play(h)
    b1, b2 = _give(h, p, "bang"), _give(h, p, "bang")
    t = h.state.next_alive_player(p.player_id).player_id
    assert h.game.action_play_card(p.player_id, b1.uid, t).ok
    assert not h.game.action_play_card(p.player_id, b2.uid, t).ok


def test_cannot_target_self(make_game):
    h = make_game(4)
    p = _ready_to_play(h)
    bang = _give(h, p, "bang")
    assert not h.game.action_play_card(p.player_id, bang.uid, p.player_id).ok


def test_killing_blow_eliminates_and_ends_game(make_game):
    h = make_game(2)
    p = _ready_to_play(h)
    target = h.state.next_alive_player(p.player_id)
    target.hp = 1
    bang = _give(h, p, "bang")
    assert h.game.action_play_card(p.player_id, bang.uid, target.player_id).ok
    assert target.status == PlayerStatus.DEAD
    assert h.state.phase == GamePhase.FINISHED
    assert h.state.winner_ids


def test_over_limit_hand_can_always_finish_turn(make_game):
    """Regression: end_turn used to demand discards that were never legal."""
    h = make_game(4)
    p = _ready_to_play(h)
    while p.hand_count() <= p.hp:
        _give(h, p, "bang")
    first = p.player_id

    assert h.game.action_end_turn(first).ok            # PLAY -> DISCARD
    assert h.state.turn.phase == TurnPhase.DISCARD
    assert not h.game.action_end_turn(first).ok        # still over the limit
    while p.hand_count() > p.hp:
        assert h.game.action_discard_card(first, p.hand[0].uid).ok
    assert h.game.action_end_turn(first).ok
    assert h.current.player_id != first


def test_failed_bang_does_not_consume_bang_allowance(make_game):
    """Regression: an illegal Bang! used to burn the turn's Bang! allowance."""
    h = make_game(4)
    p = _ready_to_play(h)
    bang = _give(h, p, "bang")
    assert not h.game.action_play_card(p.player_id, bang.uid).ok  # no target
    t = h.state.next_alive_player(p.player_id).player_id
    assert h.game.action_play_card(p.player_id, bang.uid, t).ok


def test_effects_resolve_inside_a_running_event_loop(make_game):
    """Regression: with a running loop (as under FastAPI) effects used to be
    scheduled on that loop, blocked for 5s, and never applied."""
    import asyncio

    h = make_game(4)
    p = _ready_to_play(h)
    bang = _give(h, p, "bang")
    target = h.state.next_alive_player(p.player_id)
    hp = target.hp

    async def play():
        return h.game.action_play_card(p.player_id, bang.uid, target.player_id)

    # Fresh thread: a sync-Playwright session elsewhere in the run can leave
    # a loop running in the main thread, which asyncio.run() would reject.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(asyncio.run, play()).result(timeout=10).ok
    assert target.hp == hp - 1
