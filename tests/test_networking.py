"""WebSocket integration tests using Starlette's in-process TestClient."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from fastapi.testclient import TestClient

from networking.server import app, registry


_pool = ThreadPoolExecutor(max_workers=8)


def _recv(ws, timeout: float = 5.0) -> dict:
    """receive_json with a timeout, so a missing message fails instead of hanging."""
    future = _pool.submit(ws.receive_json)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        raise AssertionError("timed out waiting for a message from the server") from None


def _drain_until(ws, wanted: str, limit: int = 20) -> dict:
    for _ in range(limit):
        msg = _recv(ws)
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
            a.send_json({"type": "set_mode", "payload": {"mode": "rezba"}})

            a.send_json({"type": "set_mode", "payload": {"mode": "rezba"}})
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
            a.send_json({"type": "set_mode", "payload": {"mode": "rezba"}})
            a.send_json({"type": "start_game", "payload": {}})

            hands = {id(a): _drain_until(a, "private_hand")["payload"],
                     id(b): _drain_until(b, "private_hand")["payload"]}
            turn = next(
                m for m in (_recv(a) for _ in range(20))
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
            a.send_json({"type": "set_mode", "payload": {"mode": "rezba"}})
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


def _state(ws, phase: str | None = None, round_: int | None = None) -> dict:
    """Next vydrz_state, optionally skipping older ones until phase/round match."""
    for _ in range(10):
        st = _drain_until(ws, "vydrz_state")["payload"]
        if (phase is None or st["phase"] == phase) and (round_ is None or st["round"] == round_):
            return st
    raise AssertionError("expected vydrz_state never arrived")


def test_vydrz_full_round_over_websocket():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomV?display_name=Alice") as a, \
             c.websocket_connect("/ws/roomV?display_name=Bob") as b:
            _drain_until(a, "joined")
            _drain_until(b, "joined")

            # Only the host may change the mode; unknown modes are refused.
            b.send_json({"type": "set_mode", "payload": {"mode": "vydrz"}})
            assert _drain_until(b, "error")["payload"]["code"] == "not_host"
            a.send_json({"type": "set_mode", "payload": {"mode": "nope"}})
            assert _drain_until(a, "error")["payload"]["code"] == "unknown_mode"
            a.send_json({"type": "set_mode", "payload": {"mode": "vydrz"}})
            room_state = _drain_until(a, "room_state")["payload"]
            assert room_state["mode"] == "vydrz"
            assert {m["id"] for m in room_state["modes"]} == {"rezba", "vydrz", "vydrz_rucne"}

            a.send_json({"type": "start_game", "payload": {}})
            sa, sb = _state(a), _state(b)
            assert sa["phase"] == "choosing" and sa["round"] == 1
            me_a = next(p for p in sa["players"] if p["player_id"] == sa["you"])
            me_b = next(p for p in sb["players"] if p["player_id"] == sb["you"])
            assert len(me_a["locations"]) == 8 and len(me_a["existences"]) == 8

            def choose(ws, me):
                ws.send_json({"type": "vydrz_choose", "payload": {
                    "location_uid": me["locations"][0]["uid"],
                    "existence_uid": me["existences"][0]["uid"]}})

            # Another player's card is rejected.
            a.send_json({"type": "vydrz_choose", "payload": {
                "location_uid": me_b["locations"][0]["uid"],
                "existence_uid": me_a["existences"][0]["uid"]}})
            assert _drain_until(a, "error")["payload"]["code"] == "action_failed"

            choose(a, me_a)
            hidden = next(p for p in _state(b)["players"] if p["player_id"] == me_a["player_id"])
            assert hidden["locked"] and hidden["pick"] is None      # hidden from Bob
            choose(b, me_b)

            after = _state(a, phase="reveal")
            assert after["phase"] == "reveal"
            assert len(after["last_result"]["drawn"]) == 3

            # Only the host can continue.
            b.send_json({"type": "vydrz_next_round", "payload": {}})
            assert _drain_until(b, "error")["payload"]["code"] == "not_host"
            a.send_json({"type": "vydrz_next_round", "payload": {}})
            nxt = _state(a, phase="choosing", round_=2)
            assert nxt["phase"] == "choosing" and nxt["round"] == 2


def test_manual_vydrz_over_websocket():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomM?display_name=Alice") as a, \
             c.websocket_connect("/ws/roomM?display_name=Bob") as b:
            _drain_until(a, "joined")
            _drain_until(b, "joined")
            a.send_json({"type": "set_mode", "payload": {"mode": "vydrz_rucne"}})
            for _ in range(5):                      # skip the room_state from the joins
                if _drain_until(a, "room_state")["payload"]["mode"] == "vydrz_rucne":
                    break
            else:
                raise AssertionError("mode never changed")
            a.send_json({"type": "start_game", "payload": {}})
            sa, sb = _state(a), _state(b)
            assert sa["mode"] == "vydrz_rucne" and sa["phase"] == "planning"

            def plan(ws, st):
                me = next(p for p in st["players"] if p["player_id"] == st["you"])
                ws.send_json({"type": "vydrz_plan", "payload": {"pairs": [
                    {"location_uid": l["uid"], "existence_uid": e["uid"]}
                    for l, e in zip(me["locations"], me["existences"])]}})

            # The automatic message is rejected in the manual mode.
            a.send_json({"type": "vydrz_choose", "payload": {"location_uid": "x", "existence_uid": "y"}})
            assert _drain_until(a, "error")["payload"]["code"] == "wrong_mode"

            plan(a, sa)
            plan(b, sb)
            sa2 = _state(a, phase="calculating")
            assert len(sa2["calc"]["drinks"]) == 3 and sa2["calc"]["mine"] is not None

            # Typing a number: the server decides what it means. A huge number is "svindlir"
            # (counts as -50); two cheaters tie at -50, which starts a manual tie-break.
            a.send_json({"type": "vydrz_submit", "payload": {"value": 150}})
            assert _drain_until(a, "action_ack")["payload"]["action"] == "vydrz_submit"
            a.send_json({"type": "vydrz_submit", "payload": {"value": 5}})
            assert _drain_until(a, "error")["payload"]["code"] == "action_failed"      # already entered
            for _ in range(6):                      # Bob's queue also holds older snapshots
                mid = _state(b, phase="calculating")
                if mid["calc"]["entries"]:
                    break
            assert any(e["tag"] == "svindlir" for e in mid["calc"]["entries"].values())
            assert "correct" not in str(mid["calc"]["entries"])
            b.send_json({"type": "vydrz_submit", "payload": {"value": 150}})
            tie = _state(a, phase="calculating")
            assert tie["calc"]["tiebreak"] is True and len(tie["calc"]["drinks"]) == 1



def _room_players(ws):
    """Latest room_state player ids/host from a client's queue."""
    last = None
    for _ in range(12):
        msg = _recv(ws)
        if msg["type"] == "room_state":
            last = msg["payload"]
            break
    return last


def test_leaving_the_lobby_removes_the_player_and_passes_the_host_on():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomL?display_name=Alice") as a, \
             c.websocket_connect("/ws/roomL?display_name=Bob") as b:
            ja = _drain_until(a, "joined")["payload"]["player_id"]
            jb = _drain_until(b, "joined")["payload"]["player_id"]
            a.send_json({"type": "leave_room", "payload": {}})
            for _ in range(10):                                # Bob sees Alice go and becomes host
                rs = _drain_until(b, "room_state")["payload"]
                if ja not in [p["player_id"] for p in rs["players"]]:
                    break
            assert [p["player_id"] for p in rs["players"]] == [jb]
            assert rs["host_player_id"] == jb


def test_game_does_not_resolve_until_every_player_has_acted_and_a_leaver_does_not_block():
    with TestClient(app) as c:
        with c.websocket_connect("/ws/roomW?display_name=A") as a, \
             c.websocket_connect("/ws/roomW?display_name=B") as b, \
             c.websocket_connect("/ws/roomW?display_name=C") as cc:
            for ws in (a, b, cc):
                _drain_until(ws, "joined")
            a.send_json({"type": "set_mode", "payload": {"mode": "vydrz"}})
            a.send_json({"type": "start_game", "payload": {}})
            states = {name: _state(ws) for name, ws in (("a", a), ("b", b), ("c", cc))}

            def pick(ws, st):
                me = next(p for p in st["players"] if p["player_id"] == st["you"])
                ws.send_json({"type": "vydrz_choose", "payload": {
                    "location_uid": me["locations"][0]["uid"], "existence_uid": me["existences"][0]["uid"]}})

            pick(a, states["a"])
            pick(b, states["b"])
            for _ in range(6):                      # two of three have picked: still choosing
                st = _drain_until(a, "vydrz_state")["payload"]
                if sum(p["locked"] for p in st["players"]) == 2:
                    assert st["phase"] == "choosing"
                    break
            cc.send_json({"type": "leave_room", "payload": {}})        # the third player leaves instead
            done = _state(a, phase="reveal")                           # the game does not wait for them forever
            assert done["last_result"]["round"] == 1
            assert any(p["away"] for p in done["players"])


def test_client_files_are_revalidated_so_updates_are_not_hidden_by_the_cache():
    with TestClient(app) as c:
        for path in ("/", "/js/app.js", "/css/vydrz.css"):
            r = c.get(path)
            assert r.status_code == 200
            assert r.headers["cache-control"] == "no-cache"
            assert r.headers.get("etag")
        etag = c.get("/js/app.js").headers["etag"]
        assert c.get("/js/app.js", headers={"If-None-Match": etag}).status_code == 304
