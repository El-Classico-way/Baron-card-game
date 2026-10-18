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
