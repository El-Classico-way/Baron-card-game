"""
server/networking/client.py

ConnectedClient — represents one live WebSocket connection.

RESPONSIBILITIES:
  - Own the WebSocket object for one connection.
  - Provide a non-blocking send() that queues outbound messages.
  - Track the mapping between a persistent player_id and the transient
    WebSocket connection so reconnects work without losing game state.
  - Expose connection health (is_connected, last_seen).

WHAT THIS CLASS IS NOT:
  - Not responsible for game logic.
  - Not responsible for parsing/dispatching inbound messages
    (that's Room's job).
  - Not responsible for knowing which room it belongs to
    (Room holds a reference to ConnectedClient, not the reverse).

RECONNECT MODEL:
  Each player is assigned a player_id (stable across reconnects) and a
  reconnect_token (opaque, issued at join, must be presented to resume).

  When a socket drops:
    1. ConnectedClient.mark_disconnected() is called.
    2. Room keeps the slot alive for RECONNECT_GRACE_SECONDS.
    3. If the player reconnects with the correct token within that window,
       Room.handle_reconnect() replaces the underlying websocket on the
       same ConnectedClient — no new player_id, no lost game state.
    4. If the window expires, Room removes the slot (player is eliminated
       or game continues shorthanded, depending on mode).

SEND QUEUE:
  FastAPI's WebSocket.send_text() is not thread-safe and must be called
  from a single coroutine.  We use asyncio.Queue to decouple producers
  (event handlers, room broadcasts) from the single sender coroutine
  (_drain_queue) that owns the socket write.

  This means send() never blocks the caller — it just enqueues.
  Back-pressure: if the queue exceeds MAX_QUEUE_SIZE, the oldest item
  is dropped and a warning is logged (prevents slow clients from
  blocking the room).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# How long (seconds) to keep a disconnected player's slot before eviction.
RECONNECT_GRACE_SECONDS: float = 30.0

# Maximum outbound messages queued per client before oldest are dropped.
MAX_QUEUE_SIZE: int = 64


class ConnectionState(str, Enum):
    """Lifecycle of a single WebSocket session."""
    CONNECTED    = "connected"
    DISCONNECTED = "disconnected"
    EVICTED      = "evicted"      # grace period expired, slot released


@dataclass
class ConnectedClient:
    """
    Represents one player's connection to a room.

    A ConnectedClient is created when a player first joins.  It persists
    across reconnects — only the underlying websocket is swapped.

    Fields:
        player_id         Stable game-layer identifier (matches Player.player_id).
        display_name      Human-readable name for UI.
        reconnect_token   Opaque string the client stores to resume sessions.
        websocket         The live FastAPI WebSocket, or None if disconnected.
        state             Current connection lifecycle state.
        connected_at      Timestamp of most recent successful connection.
        disconnected_at   Timestamp of most recent disconnect (None if connected).
        _send_queue       Internal async queue of outbound message dicts.
        _drain_task       The asyncio Task draining the send queue.

    Usage::

        client = ConnectedClient.create(websocket=ws, display_name="Alice")
        await client.send(mk_joined(...))
        # later, on disconnect:
        await client.mark_disconnected()
        # on reconnect:
        await client.reconnect(new_ws)
    """

    player_id: str
    display_name: str
    reconnect_token: str
    websocket: WebSocket | None
    state: ConnectionState = ConnectionState.CONNECTED
    connected_at: float = field(default_factory=time.time)
    disconnected_at: float | None = None

    _send_queue: asyncio.Queue[dict | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=MAX_QUEUE_SIZE),
        repr=False,
    )
    _drain_task: asyncio.Task | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, websocket: WebSocket, display_name: str) -> "ConnectedClient":
        """
        Create a new client slot for a freshly joined player.
        Generates stable player_id and reconnect_token.
        """
        return cls(
            player_id=uuid.uuid4().hex,
            display_name=display_name,
            reconnect_token=uuid.uuid4().hex,
            websocket=websocket,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Begin draining the send queue.
        Must be called once after creation and once after each reconnect.
        The drain task runs until the connection closes.
        """
        self._drain_task = asyncio.create_task(
            self._drain_queue(),
            name=f"drain-{self.player_id[:8]}",
        )

    async def mark_disconnected(self) -> None:
        """
        Called when the WebSocket closes unexpectedly or gracefully.
        Stops the drain task; preserves the player slot for reconnection.
        """
        self.state = ConnectionState.DISCONNECTED
        self.disconnected_at = time.time()

        # Signal drain task to stop by enqueuing a sentinel.
        await self._send_queue.put(None)

        if self._drain_task and not self._drain_task.done():
            try:
                await asyncio.wait_for(self._drain_task, timeout=2.0)
            except asyncio.TimeoutError:
                self._drain_task.cancel()

        self.websocket = None
        logger.info("Client %s (%s) disconnected.", self.player_id[:8], self.display_name)

    async def reconnect(self, websocket: WebSocket) -> None:
        """
        Swap in a new WebSocket for the same player slot.
        Resets queue and starts a new drain task.
        """
        # Drain any stale entries from the old queue.
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self.websocket = websocket
        self.state = ConnectionState.CONNECTED
        self.connected_at = time.time()
        self.disconnected_at = None

        await self.start()
        logger.info("Client %s (%s) reconnected.", self.player_id[:8], self.display_name)

    def evict(self) -> None:
        """
        Mark the slot as permanently released after the grace period expires.
        Room will remove this client and inform the game engine.
        """
        self.state = ConnectionState.EVICTED
        logger.info(
            "Client %s (%s) evicted after grace period.",
            self.player_id[:8], self.display_name,
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(self, packet: dict[str, Any]) -> None:
        """
        Enqueue an outbound packet for delivery.

        Non-blocking — the caller is never suspended waiting for the socket.
        If the queue is full, the oldest item is dropped to prevent
        a slow client from stalling the room.
        """
        if self.state == ConnectionState.EVICTED:
            return

        if self._send_queue.full():
            try:
                dropped = self._send_queue.get_nowait()
                logger.warning(
                    "Send queue full for client %s — dropped: %s",
                    self.player_id[:8],
                    dropped.get("type", "?") if dropped else "sentinel",
                )
            except asyncio.QueueEmpty:
                pass

        await self._send_queue.put(packet)

    async def send_now(self, packet: dict[str, Any]) -> None:
        """
        Bypass the queue and write directly to the socket.

        USE SPARINGLY — only for urgent control messages where ordering
        relative to queued items doesn't matter (e.g. a final error before
        closing the socket).  Concurrent calls are not safe.
        """
        if self.websocket is None:
            return
        try:
            await self.websocket.send_text(json.dumps(packet))
        except Exception as exc:
            logger.debug("send_now failed for %s: %s", self.player_id[:8], exc)

    async def _drain_queue(self) -> None:
        """
        Background task: pull messages from the queue and write to the socket.

        Runs until a None sentinel is dequeued (disconnect signal) or the
        socket raises.  All socket errors are caught — they are expected when
        a client drops.
        """
        while True:
            packet = await self._send_queue.get()

            if packet is None:
                # Sentinel — clean shutdown.
                break

            if self.websocket is None:
                # Socket was cleared before we got here; discard.
                continue

            try:
                await self.websocket.send_text(json.dumps(packet))
            except Exception as exc:
                # Socket likely closed — stop draining.
                logger.debug(
                    "Drain error for client %s: %s", self.player_id[:8], exc
                )
                break

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    @property
    def seconds_disconnected(self) -> float | None:
        """Seconds since disconnect, or None if currently connected."""
        if self.disconnected_at is None:
            return None
        return time.time() - self.disconnected_at

    @property
    def grace_expired(self) -> bool:
        """True if the reconnect window has passed."""
        secs = self.seconds_disconnected
        if secs is None:
            return False
        return secs > RECONNECT_GRACE_SECONDS

    def __repr__(self) -> str:
        return (
            f"ConnectedClient(id={self.player_id[:8]!r}, "
            f"name={self.display_name!r}, state={self.state})"
        )
