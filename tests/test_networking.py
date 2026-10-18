"""WebSocket integration tests using Starlette's in-process TestClient."""
from fastapi.testclient import TestClient

from networking.server import app, registry


def _drain_until(ws, wanted: str, limit: int = 20) -> dict:
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == wanted:
            return msg
    raise AssertionError(f"never received {wanted!r}")


def test_create_and_list_rooms():
    with TestClient(app) as c:
        rid = c.post("/lobby/rooms").json()["room_id"]
        assert c.get(f"/lobby/rooms/{rid}").status_code == 200
        assert c.get("/lobby/rooms/does-not-exist").status_code == 404


def test_join_requires_display_name():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomA") as ws:
            assert _drain_until(ws, "error")


def test_join_and_bad_frames_do_not_close_socket():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomB?display_name=Alice") as ws:
            assert _drain_until(ws, "joined")
            ws.send_text("not json")
            assert _drain_until(ws, "error")
            ws.send_json({"type": "nonsense", "payload": {}})
            assert _drain_until(ws, "error")
            ws.send_json({"type": "chat", "payload": {"message": "hi"}})
            assert _drain_until(ws, "chat")["payload"]["message"] == "hi"


def test_two_players_see_each_other():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomC?display_name=Alice") as a:
            _drain_until(a, "joined")
            with c.websocket_connect("/ws/roomC?display_name=Bob") as b:
                _drain_until(b, "joined")
                assert _drain_until(a, "player_connected")


def test_host_starts_game_and_players_get_hands():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomD?display_name=Alice") as a, \
             c.websocket_connect("/ws/roomD?display_name=Bob") as b:
            _drain_until(a, "joined")
            _drain_until(b, "joined")

            # Non-host is refused.
            b.send_json({"type": "start_game", "payload": {}})
            assert _drain_until(b, "error")["payload"]["code"] == "not_host"

            a.send_json({"type": "start_game", "payload": {}})
            assert _drain_until(a, "private_hand")
            assert _drain_until(b, "private_hand")


def test_hand_is_refreshed_after_discarding():
    """Regression: the server never sent an updated private_hand after a
    discard/play, so the client's hand stayed stale."""
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomE?display_name=Alice") as a, \
             c.websocket_connect("/ws/roomE?display_name=Bob") as b:
            _drain_until(a, "joined")
            _drain_until(b, "joined")
            a.send_json({"type": "start_game", "payload": {}})

            hands = {id(a): _drain_until(a, "private_hand")["payload"],
                     id(b): _drain_until(b, "private_hand")["payload"]}
            turn = next(
                m for m in (a.receive_json() for _ in range(20))
                if m["type"] == "game_event"
                and m["payload"]["event_type"] == "turn_started"
            )
            mover_id = turn["payload"]["data"]["player_id"]
            ws = a if hands[id(a)]["player_id"] == mover_id else b
            start = len(hands[id(ws)]["hand"])

            ws.send_json({"type": "draw_cards", "payload": {}})
            drawn = _drain_until(ws, "private_hand")["payload"]["hand"]
            assert len(drawn) == start + 2

            # Hand now exceeds the limit: end_turn enters the discard phase.
            ws.send_json({"type": "end_turn", "payload": {}})
            _drain_until(ws, "action_ack")
            ws.send_json({"type": "discard_card",
                          "payload": {"card_uid": drawn[0]["uid"]}})
            after = _drain_until(ws, "private_hand")["payload"]["hand"]
            assert len(after) == len(drawn) - 1
            assert drawn[0]["uid"] not in {c["uid"] for c in after}


def test_bang_over_websocket_deals_damage():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomF?display_name=Alice") as a, \
             c.websocket_connect("/ws/roomF?display_name=Bob") as b:
            _drain_until(a, "joined")
            _drain_until(b, "joined")
            room = registry._rooms["roomF"]
            a.send_json({"type": "start_game", "payload": {}})
            _drain_until(a, "private_hand")
            _drain_until(b, "private_hand")

            state = room._state
            mover = state.get_player(state.turn.player_id)
            ws = a if mover.display_name == "Alice" else b
            ws.send_json({"type": "draw_cards", "payload": {}})
            _drain_until(ws, "action_ack")

            deck = state.deck._draw_pile
            bang = next(x for x in deck if x.id == "bang")
            deck.remove(bang)
            mover.deal(bang)
            victim = state.next_alive_player(mover.player_id)
            hp = victim.hp

            ws.send_json({"type": "play_card", "payload": {
                "card_uid": bang.uid, "target_id": victim.player_id}})
            _drain_until(ws, "action_ack")
            assert victim.hp == hp - 1
