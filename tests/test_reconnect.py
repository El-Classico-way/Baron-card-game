"""Reconnect, rejoin, heartbeat, winner announcement and return-to-lobby over WebSocket."""
import time
from contextlib import ExitStack

from fastapi.testclient import TestClient

from networking.server import app
from tests.test_networking import _drain_until, _state


def _start(c, stack: ExitStack, room, names=("Alice", "Bob"), mode="vydrz"):
    sockets = [stack.enter_context(c.websocket_connect(f"/ws/{room}?display_name={n}")) for n in names]
    joined = [_drain_until(ws, "joined")["payload"] for ws in sockets]
    sockets[0].send_json({"type": "set_mode", "payload": {"mode": mode}})
    sockets[0].send_json({"type": "start_game", "payload": {}})
    return sockets, joined


def _choose_first(ws, st):
    me = next(p for p in st["players"] if p["player_id"] == st["you"])
    ws.send_json({"type": "vydrz_choose", "payload": {
        "location_uid": me["locations"][0]["uid"], "existence_uid": me["existences"][0]["uid"]}})


def _play_auto_game(host, guest):
    """Both players pick their first cards every round; the host moves on. Returns the final state."""
    done = None
    for rnd in range(1, 9):
        _choose_first(host, _state(host, "choosing", rnd))
        _choose_first(guest, _state(guest, "choosing", rnd))
        done = _state(host, "reveal" if rnd < 8 else "finished")
        if rnd < 8:
            host.send_json({"type": "vydrz_next_round", "payload": {}})
    return done


def test_ping_gets_pong():
    with TestClient(app) as c, c.websocket_connect("/ws/roomPing?display_name=A") as a:
        _drain_until(a, "joined")
        a.send_json({"type": "ping", "payload": {}})
        assert _drain_until(a, "pong")["type"] == "pong"


def test_reconnect_token_resumes_with_joined_and_state():
    with TestClient(app) as c, ExitStack() as stack:
        (a, b), (ja, jb) = _start(c, stack, "roomTk")
        _state(a); _state(b)
        with c.websocket_connect(f"/ws/roomTk?token={jb['reconnect_token']}") as b2:
            joined = _drain_until(b2, "joined")["payload"]
            assert joined["player_id"] == jb["player_id"]
            assert _state(b2)["you"] == jb["player_id"]
            # The old socket closing late must not make the player look disconnected.
            b.close()
            time.sleep(0.2)
            st = _state(a)
            assert all(p["connected"] for p in st["players"])


def test_bad_token_is_a_fatal_error():
    with TestClient(app) as c, c.websocket_connect("/ws/roomBad?token=nope") as ws:
        assert _drain_until(ws, "error")["payload"]["code"] == "invalid_token"


def test_rejoin_by_name_during_game_name_taken_and_strangers_refused():
    with TestClient(app) as c, ExitStack() as stack:
        (a, b), _ = _start(c, stack, "roomRn")
        _state(a); _state(b)
        b.close()                                                      # Bob's connection dies
        time.sleep(0.2)
        with c.websocket_connect("/ws/roomRn?display_name=bob") as b2:   # same name, any case
            joined = _drain_until(b2, "joined")["payload"]
            assert joined["display_name"] == "Bob"
            assert _state(b2)["you"] == joined["player_id"]
            with c.websocket_connect("/ws/roomRn?display_name=Bob") as b3:
                assert _drain_until(b3, "error")["payload"]["code"] == "name_taken"
            with c.websocket_connect("/ws/roomRn?display_name=Cara") as b4:
                assert _drain_until(b4, "error")["payload"]["code"] == "room_not_joinable"
            st = _state(a)
            while not all(p["connected"] for p in st["players"]):
                st = _state(a)


def test_winner_is_hidden_until_the_host_announces_and_lobby_return_works():
    with TestClient(app) as c, ExitStack() as stack:
        (host, guest), _ = _start(c, stack, "roomAn")
        done = _play_auto_game(host, guest)
        assert done["winner_announced"] is False
        assert done["last_result"]["final"] is None and done["last_result"]["final_pending"] is True

        guest.send_json({"type": "vydrz_announce", "payload": {}})
        assert _drain_until(guest, "error")["payload"]["code"] == "not_host"
        guest.send_json({"type": "return_to_lobby", "payload": {}})
        assert _drain_until(guest, "error")["payload"]["code"] == "not_host"

        host.send_json({"type": "vydrz_announce", "payload": {}})
        for ws in (host, guest):
            st = _state(ws, "finished")
            while not st["winner_announced"]:
                st = _state(ws, "finished")
            assert st["last_result"]["final"]["ranking"]

        host.send_json({"type": "return_to_lobby", "payload": {}})
        for ws in (host, guest):
            rs = _drain_until(ws, "room_state")["payload"]
            while rs["game_phase"] != "lobby":
                rs = _drain_until(ws, "room_state")["payload"]
            assert len(rs["players"]) == 2

        # The same people play again, here in the other variant, without a new code.
        host.send_json({"type": "set_mode", "payload": {"mode": "vydrz_rucne"}})
        host.send_json({"type": "start_game", "payload": {}})
        st = _state(guest)
        assert st["mode"] == "vydrz_rucne" and st["phase"] == "planning"


def test_return_to_lobby_only_after_the_game_ended():
    with TestClient(app) as c, ExitStack() as stack:
        (host, guest), _ = _start(c, stack, "roomEarly")
        _state(host)
        host.send_json({"type": "return_to_lobby", "payload": {}})
        assert _drain_until(host, "error")["payload"]["code"] == "action_failed"


def test_host_can_drop_a_disconnected_player_so_the_game_goes_on():
    with TestClient(app) as c, ExitStack() as stack:
        (host, guest), (_, jg) = _start(c, stack, "roomDr")
        sh = _state(host); _state(guest)
        host.send_json({"type": "drop_player", "payload": {"player_id": jg["player_id"]}})
        assert _drain_until(host, "error")["payload"]["code"] == "action_failed"     # still connected
        guest.close()
        time.sleep(0.2)
        _choose_first(host, sh)                    # the game waits for Bob until Alice removes him
        host.send_json({"type": "drop_player", "payload": {"player_id": jg["player_id"]}})
        done = _state(host, "reveal")
        assert any(p["away"] for p in done["players"])
