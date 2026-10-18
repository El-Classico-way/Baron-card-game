"""
server/networking/server.py

FastAPI application — WebSocket endpoint, room registry, HTTP lobby routes.

RESPONSIBILITIES:
  - Define the FastAPI app and all routes.
  - Manage the in-memory room registry (room_id → Room).
  - Accept WebSocket connections and hand them off to the right Room.
  - Handle the full WebSocket lifecycle: connect → receive loop → disconnect.
  - Parse and validate inbound messages at the boundary (using protocol.py).
  - Provide HTTP endpoints for lobby management (list rooms, create room).

WHAT SERVER DOES NOT DO:
  - No game logic.
  - No message business logic — that is all in Room.
  - No serialisation decisions — those are in protocol.py.

WEBSOCKET LIFECYCLE
───────────────────
  1. Client connects to /ws/{room_id}?token=<reconnect_token>
       - If token is present and valid  → Room.reconnect()
       - If token absent or unknown     → ConnectedClient.create() + Room.join()
  2. Server enters the receive loop.
  3. Each raw text frame is JSON-decoded and parsed via parse_inbound().
       - Parse error  → send mk_error() and continue (don't close).
       - Unknown type → Room.handle_message() sends its own error.
  4. On WebSocket close (any reason): Room.handle_disconnect().

ROOM REGISTRY
─────────────
  Rooms are kept in a plain dict protected by an asyncio.Lock.
  The lock is only held during O(1) dict mutations — never during I/O —
  so there is no meaningful contention.

  Rooms are reaped lazily: any request that looks up a room checks
  whether it is in RoomPhase.EMPTY and removes it.

RUNNING THE SERVER
──────────────────
  Development::

      uvicorn networking.server:app --reload --port 8000

  Production (from project root)::

      uvicorn networking.server:app --host 0.0.0.0 --port 8000 --workers 1

  WHY SINGLE WORKER:
    The room registry is in-process memory.  Multiple workers would have
    separate registries.  For multi-worker deployments, replace the dict
    with Redis-backed shared state (future work).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from networking.client import ConnectedClient
from networking.protocol import mk_error, parse_inbound
from networking.room import Room, RoomPhase

from cards import load_default_registry
from game.card import Card
from modes.vydrz import VydrzContent, load_default_content

logger = logging.getLogger(__name__)

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"


# ---------------------------------------------------------------------------
# Room registry
# ---------------------------------------------------------------------------

_vydrz_content = None


def _vydrz() -> "VydrzContent":
    """Výdrž card data, loaded once."""
    global _vydrz_content
    if _vydrz_content is None:
        _vydrz_content = load_default_content()
    return _vydrz_content


def _default_deck() -> list[Card]:
    """Fresh deck from the base card pack (new Card instances every game)."""
    return load_default_registry().build_deck()


class RoomRegistry:
    """
    Thread-safe (asyncio-safe) in-memory registry of active rooms.

    All mutations are protected by a single asyncio.Lock so concurrent
    WebSocket handlers see a consistent view.

    Usage::

        registry = RoomRegistry()
        room = await registry.get_or_create("abc123")
        room = await registry.get("abc123")   # None if not found
        await registry.remove("abc123")
        rooms = await registry.list_rooms()
    """

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock: Any = None   # initialised lazily after event loop starts

    def _get_lock(self):
        # asyncio.Lock must be created inside a running event loop.
        if self._lock is None:
            import asyncio
            self._lock = asyncio.Lock()
        return self._lock

    async def get(self, room_id: str) -> Room | None:
        async with self._get_lock():
            return self._rooms.get(room_id)

    async def get_or_create(self, room_id: str) -> Room:
        async with self._get_lock():
            room = self._rooms.get(room_id)
            if room is None:
                room = Room.create(room_id=room_id, deck_factory=_default_deck, vydrz_content=_vydrz())
                self._rooms[room_id] = room
                logger.info("Created room %s.", room_id)
            return room

    async def create_new(self) -> Room:
        """Create a room with a server-generated id and register it."""
        room = Room.create(deck_factory=_default_deck, vydrz_content=_vydrz())
        async with self._get_lock():
            self._rooms[room.room_id] = room
        logger.info("Created room %s (server-generated id).", room.room_id)
        return room

    async def remove(self, room_id: str) -> None:
        async with self._get_lock():
            self._rooms.pop(room_id, None)

    async def list_rooms(self) -> list[dict]:
        """Return public summaries of all non-empty rooms."""
        async with self._get_lock():
            rooms = list(self._rooms.values())

        # Reap empty rooms.
        empty_ids = [r.room_id for r in rooms if r.phase == RoomPhase.EMPTY]
        for room_id in empty_ids:
            await self.remove(room_id)

        return [
            r.to_dict()
            for r in rooms
            if r.phase != RoomPhase.EMPTY
        ]

    async def reap_empty(self) -> int:
        """Remove empty rooms. Returns count removed."""
        async with self._get_lock():
            empty = [rid for rid, r in self._rooms.items()
                     if r.phase == RoomPhase.EMPTY]
            for rid in empty:
                del self._rooms[rid]
        return len(empty)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

# Module-level registry — shared across all requests in the process.
registry = RoomRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.
    Runs setup before the server starts accepting requests.
    """
    logger.info("Game server starting up.")
    yield
    logger.info("Game server shutting down.")


def create_app() -> FastAPI:
    """
    Build and configure the FastAPI application.

    Separated from module-level instantiation so tests can call
    create_app() to get a fresh instance with a clean registry.
    """
    app = FastAPI(
        title="Card Game Server",
        description="Multiplayer card game — WebSocket API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],    # tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Attach routes.
    app.include_router(lobby_router)
    app.include_router(ws_router)

    # Serve the browser client last so API/WebSocket routes take precedence.
    if CLIENT_DIR.is_dir():
        app.mount("/", StaticFiles(directory=CLIENT_DIR, html=True), name="client")

    return app


# ---------------------------------------------------------------------------
# HTTP lobby routes
# ---------------------------------------------------------------------------

from fastapi import APIRouter

lobby_router = APIRouter(prefix="/lobby", tags=["lobby"])


@lobby_router.get("/rooms")
async def list_rooms() -> list[dict]:
    """
    Return all active, non-empty rooms.

    Response::

        [
          {
            "room_id": "abc123",
            "phase": "lobby",
            "player_count": 2,
            "max_players": 7,
            "players": [...]
          }
        ]
    """
    return await registry.list_rooms()


@lobby_router.post("/rooms")
async def create_room() -> dict:
    """
    Create a new room with a server-generated id.

    Response::

        { "room_id": "abc123" }

    The creator should then connect via WebSocket to /ws/{room_id}.
    """
    room = await registry.create_new()
    return {"room_id": room.room_id}


@lobby_router.get("/rooms/{room_id}")
async def get_room(room_id: str) -> dict:
    """Return the current state of a specific room."""
    room = await registry.get(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found.")
    return room.to_dict()


# ---------------------------------------------------------------------------
# WebSocket routes
# ---------------------------------------------------------------------------

ws_router = APIRouter(tags=["websocket"])


@ws_router.websocket("/ws/{room_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_id: str,
    token: str | None = Query(default=None),
    display_name: str | None = Query(default=None),
) -> None:
    """
    WebSocket endpoint for game communication.

    Connection URL::

        ws://host/ws/{room_id}?display_name=Alice
        ws://host/ws/{room_id}?token=<reconnect_token>

    Query parameters:
        display_name   Required for new connections.
        token          Reconnect token for resuming a dropped session.

    Protocol:
        All frames are JSON objects with { "type": str, "payload": dict }.
        See protocol.py for the full message catalogue.

    Disconnect:
        The server never closes the WebSocket proactively during a game.
        Clients may disconnect at any time; the room holds the slot for
        RECONNECT_GRACE_SECONDS before evicting the player.
    """
    await websocket.accept()

    room = await registry.get_or_create(room_id)
    client: ConnectedClient | None = None

    try:
        # --- reconnect or new join ---
        if token:
            success = await room.reconnect(token, websocket)
            if not success:
                await websocket.send_text(json.dumps(
                    mk_error("Neplatný nebo vypršený token pro obnovení spojení.", "invalid_token")
                ))
                await websocket.close()
                return
            # Find the client that was reconnected.
            client = next(
                (c for c in room._clients.values()
                 if c.reconnect_token == token),
                None,
            )
        else:
            if not display_name:
                await websocket.send_text(json.dumps(
                    mk_error("Je potřeba zadat jméno.", "missing_display_name")
                ))
                await websocket.close()
                return

            client = ConnectedClient.create(
                websocket=websocket,
                display_name=display_name[:32],
            )
            success = await room.join(client)
            if not success:
                await websocket.close()
                return

        if client is None:
            await websocket.close()
            return

        # --- receive loop ---
        await _receive_loop(websocket, room, client)

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected: %s", client.player_id[:8] if client else "?")
    except Exception as exc:
        logger.exception("Unhandled error in websocket_endpoint: %s", exc)
    finally:
        if client is not None:
            await room.handle_disconnect(client.player_id)

        # Reap empty room immediately.
        if room.is_empty:
            await registry.remove(room_id)


async def _receive_loop(
    websocket: WebSocket,
    room: Room,
    client: ConnectedClient,
) -> None:
    """
    Continuously read frames from the WebSocket and dispatch them.

    Parse errors never close the socket — the client gets an error
    packet and the loop continues.  This lets a buggy frontend recover
    without forcing a full reconnect.
    """
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            break
        except Exception as exc:
            logger.debug("receive_text error for %s: %s", client.player_id[:8], exc)
            break

        # --- parse envelope ---
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await client.send(mk_error("Neplatný JSON.", "parse_error"))
            continue

        # --- validate and route ---
        try:
            msg_type, payload = parse_inbound(data)
        except ValueError as exc:
            await client.send(mk_error(str(exc), "unknown_message_type"))
            continue
        except ValidationError as exc:
            # Pydantic gives us field-level detail — surface it.
            first_error = exc.errors()[0]
            msg = f"{first_error['loc']}: {first_error['msg']}"
            await client.send(mk_error(msg, "invalid_payload"))
            continue

        # join_room is handled at connection time; reject if seen mid-session.
        if msg_type == "join_room":
            await client.send(mk_error(
                "Už jsi v místnosti.",
                "already_joined",
            ))
            continue

        await room.handle_message(client, msg_type, payload)


# ---------------------------------------------------------------------------
# App instance  (import target for uvicorn)
# ---------------------------------------------------------------------------

app = create_app()
