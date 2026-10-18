"""
server/networking/room.py

Room — one multiplayer game session.

RESPONSIBILITIES:
  - Own the Game instance and EventBus for one session.
  - Track all ConnectedClients in this room.
  - Subscribe to every engine event and route them to the right clients.
  - Dispatch validated inbound messages to game action methods.
  - Enforce the hand-privacy rule: public events go to all clients;
    private hand data is sent only to the owning client.
  - Manage the reconnect grace period with asyncio timers.
  - Signal the server registry when the room is empty and can be reaped.

WHAT ROOM DOES NOT DO:
  - No game logic.  Room calls game.action_*() and lets the engine decide.
  - No WebSocket I/O.  Room calls client.send() and the client owns the socket.
  - No HTTP.  Room is created and looked up by the server layer.

EVENT → CLIENT ROUTING
──────────────────────
  Most engine events are public and go to every client as a game_event packet.
  Four events need extra work:

  CARDS_DEALT / CARD_DRAWN / STATE_SNAPSHOT:
    After broadcasting the public event, Room sends each affected player
    their updated private hand via mk_private_hand().  This is the only place
    hand privacy is enforced.

  PLAYER_ELIMINATED:
    The eliminated player's role is now public.  The public event already
    carries it, so no extra step is needed.

  REACTION_REQUESTED:
    Forwarded only to the reacting_player_ids list, not broadcast.

  ERROR (engine-emitted):
    Forwarded only to the named player_id.

INBOUND ACTION DISPATCH
───────────────────────
  Every inbound message type maps to exactly one game action method.
  The dispatcher is a plain dict keyed by message type string.
  Adding a new action = adding one entry to _action_handlers.

CARD LOADING
────────────
  Room.start_game() receives a list[Card] (the shuffled deck) from the
  caller.  Responsibility for building and loading card definitions belongs
  to the CardLoader (cards/ package, future).  Room does not know about
  JSON files or card definitions.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from fastapi import WebSocket

from game.card import Card
from game.enums import EventType, GamePhase
from game.events import (
    EventBus,
    GameEvent,
    CardsDealtEvent,
    CardDrawnEvent,
    ErrorEvent,
    PlayerEliminatedEvent,
    ReactionRequestedEvent,
    StateSnapshotEvent,
)
from game.game import Game
from game.state import GameState

from effects.base import build_default_registry

from networking.client import ConnectedClient, RECONNECT_GRACE_SECONDS
from networking.protocol import (
    # Inbound payload models
    DrawCardsPayload,
    PlayCardPayload,
    DiscardCardPayload,
    EndTurnPayload,
    StartGamePayload,
    RequestSnapshotPayload,
    ChatPayload,
    # Outbound builders
    mk_action_ack,
    mk_chat,
    mk_error,
    mk_game_event,
    mk_player_connected,
    mk_player_disconnected,
    mk_private_hand,
    mk_room_state,
    mk_state_snapshot,
)

logger = logging.getLogger(__name__)

# Maximum players allowed in a room (game engine enforces 2–7, this is the
# upper cap at the networking layer for lobby management).
MAX_PLAYERS: int = 7


# ---------------------------------------------------------------------------
# RoomPhase — lifecycle of a room, distinct from GamePhase
# ---------------------------------------------------------------------------

class RoomPhase:
    """
    Thin string constants for the room's own lifecycle.

    WHY NOT AN ENUM:
      These are only used as values in outbound packets (mk_room_state).
      String constants are simpler and serialise without .value.
    """
    LOBBY    = "lobby"
    PLAYING  = "playing"
    FINISHED = "finished"
    EMPTY    = "empty"      # all clients evicted; room can be reaped


# ---------------------------------------------------------------------------
# Room
# ---------------------------------------------------------------------------

class Room:
    """
    One multiplayer game session.

    Create via Room.create(), then add players with join().
    Call start_game() when the lobby is ready.

    Usage::

        room = Room.create(room_id="abc123")

        # When a player connects:
        client = ConnectedClient.create(websocket=ws, display_name="Alice")
        await room.join(client)

        # When a player sends a message:
        await room.handle_message(client, msg_type, payload)

        # When host starts the game:
        await room.start_game(cards)
    """

    def __init__(
        self,
        room_id: str,
        deck_factory: Callable[[], list[Card]] | None = None,
    ) -> None:
        self.room_id: str = room_id
        # Supplies the deck when the host starts the game.  Injected so the
        # room stays ignorant of card loading (see Room.start_game docs).
        self._deck_factory = deck_factory
        self.phase: str = RoomPhase.LOBBY
        self.created_at: float = time.time()

        # Clients keyed by player_id for fast lookup.
        self._clients: dict[str, ConnectedClient] = {}

        # Reconnect tokens map to player_ids for O(1) resume lookup.
        self._token_index: dict[str, str] = {}   # token → player_id

        # The first player to join is the host.
        self._host_player_id: str | None = None

        # Game layer — created in start_game().
        self._bus: EventBus = EventBus()
        self._game: Game | None = None
        self._state: GameState | None = None

        # Eviction timers keyed by player_id.
        self._eviction_tasks: dict[str, asyncio.Task] = {}

        # Subscribe to all engine events once (wired in _subscribe_to_bus).
        self._subscribe_to_bus()

    @classmethod
    def create(
        cls,
        room_id: str | None = None,
        deck_factory: Callable[[], list[Card]] | None = None,
    ) -> "Room":
        return cls(room_id=room_id or uuid.uuid4().hex[:8], deck_factory=deck_factory)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def player_count(self) -> int:
        return len(self._clients)

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self._clients.values() if c.is_connected)

    @property
    def is_empty(self) -> bool:
        return self.player_count == 0

    @property
    def is_joinable(self) -> bool:
        return self.phase == RoomPhase.LOBBY and self.player_count < MAX_PLAYERS

    # ------------------------------------------------------------------
    # Join / leave / reconnect
    # ------------------------------------------------------------------

    async def join(self, client: ConnectedClient) -> bool:
        """
        Add a new player to the lobby.

        Returns True on success, False if the room is full or already started.
        Starts the client's drain task and broadcasts updated lobby state.
        """
        if not self.is_joinable:
            await client.send(mk_error(
                "Room is full or game has already started.", "room_not_joinable"
            ))
            return False

        self._clients[client.player_id] = client
        self._token_index[client.reconnect_token] = client.player_id

        if self._host_player_id is None:
            self._host_player_id = client.player_id

        await client.start()
        logger.info(
            "Player %s (%s) joined room %s.",
            client.player_id[:8], client.display_name, self.room_id,
        )

        # Tell the joiner their IDs + reconnect token.
        from networking.protocol import mk_joined
        await client.send(mk_joined(
            room_id=self.room_id,
            player_id=client.player_id,
            reconnect_token=client.reconnect_token,
            display_name=client.display_name,
        ))

        # Tell everyone about the new lobby state.
        await self._broadcast_room_state()
        await self._broadcast(mk_player_connected(
            client.player_id, client.display_name
        ), exclude=client.player_id)

        return True

    async def reconnect(self, token: str, websocket: WebSocket) -> bool:
        """
        Attempt to resume a disconnected session using a reconnect_token.

        Returns True if the token matched a live slot and the client was
        successfully reconnected.  The caller should close the new websocket
        and send an error if this returns False.
        """
        player_id = self._token_index.get(token)
        if player_id is None:
            return False

        client = self._clients.get(player_id)
        if client is None or client.state.value == "evicted":
            return False

        # Cancel any pending eviction timer.
        self._cancel_eviction_timer(player_id)

        await client.reconnect(websocket)
        await self._broadcast(mk_player_connected(
            client.player_id, client.display_name
        ))

        # Send a full state snapshot so the client catches up.
        await self._send_snapshot_to(client)
        logger.info(
            "Player %s (%s) reconnected to room %s.",
            player_id[:8], client.display_name, self.room_id,
        )
        return True

    async def handle_disconnect(self, player_id: str) -> None:
        """
        Called by the server when a WebSocket closes unexpectedly.

        Marks the client disconnected and starts the eviction grace timer.
        Does not immediately remove the player — they may reconnect.
        """
        client = self._clients.get(player_id)
        if client is None:
            return

        await client.mark_disconnected()
        await self._broadcast(mk_player_disconnected(
            client.player_id, client.display_name
        ))

        # Start eviction timer.
        task = asyncio.create_task(
            self._eviction_timer(player_id),
            name=f"evict-{player_id[:8]}",
        )
        self._eviction_tasks[player_id] = task

    async def _eviction_timer(self, player_id: str) -> None:
        """Wait out the grace period then evict if still disconnected."""
        await asyncio.sleep(RECONNECT_GRACE_SECONDS)
        client = self._clients.get(player_id)
        if client is None or client.is_connected:
            return   # reconnected in time

        client.evict()
        del self._clients[player_id]
        if client.reconnect_token in self._token_index:
            del self._token_index[client.reconnect_token]

        logger.info(
            "Evicted player %s (%s) from room %s.",
            player_id[:8], client.display_name, self.room_id,
        )

        if self.is_empty:
            self.phase = RoomPhase.EMPTY

        # TODO: if game is in progress, notify engine to eliminate the player
        #       or pause the game.  Decision depends on game-mode rules.

    def _cancel_eviction_timer(self, player_id: str) -> None:
        task = self._eviction_tasks.pop(player_id, None)
        if task and not task.done():
            task.cancel()

    # ------------------------------------------------------------------
    # Game start
    # ------------------------------------------------------------------

    async def start_game(self, cards: list[Card]) -> bool:
        """
        Transition from lobby → in-progress.

        Builds GameState and Game from current client roster, loads the deck,
        and calls game.start().  Returns True on success.

        The caller is responsible for supplying a shuffled card list —
        Room does not know about card definitions or JSON loading.
        """
        if self.phase != RoomPhase.LOBBY:
            return False
        if self.player_count < 2:
            await self._broadcast(mk_error(
                "Need at least 2 players to start.", "not_enough_players"
            ))
            return False

        player_ids = list(self._clients.keys())
        display_names = {
            pid: c.display_name for pid, c in self._clients.items()
        }

        self._state = GameState.new(
            player_ids=player_ids,
            display_names=display_names,
        )
        registry = build_default_registry()
        self._game = Game(
            state=self._state,
            bus=self._bus,
            registry=registry,
        )

        result = self._game.start(cards)
        if not result.ok:
            await self._broadcast(mk_error(result.error, "start_failed"))
            return False

        self.phase = RoomPhase.PLAYING
        logger.info(
            "Room %s game started with %d players.", self.room_id, self.player_count
        )

        # Send each player their private snapshot (hand + role).
        for client in self._clients.values():
            if client.is_connected:
                await self._send_snapshot_to(client)

        return True

    # ------------------------------------------------------------------
    # Inbound message dispatch
    # ------------------------------------------------------------------

    async def handle_message(
        self,
        client: ConnectedClient,
        msg_type: str,
        payload: Any,
    ) -> None:
        """
        Route a validated inbound message to the correct handler.

        All game action handlers follow the same pattern:
          1. Guard: reject if game not started or wrong phase.
          2. Call game.action_*() with player_id from client.
          3. If ok → send ack to actor.
          4. If fail → send error to actor only.
        """
        handler = self._message_handlers.get(msg_type)
        if handler is None:
            await client.send(mk_error(f"Unknown action: {msg_type}", "unknown_action"))
            return

        await handler(client, payload)

    # ------------------------------------------------------------------
    # Message handlers (one per inbound type)
    # ------------------------------------------------------------------

    async def _handle_draw_cards(
        self, client: ConnectedClient, payload: DrawCardsPayload
    ) -> None:
        if not self._game:
            await client.send(mk_error("Game not started.", "game_not_started"))
            return

        result = self._game.action_draw_cards(client.player_id, payload.count)
        if result.ok:
            await client.send(mk_action_ack("draw_cards"))
            # Private hand update is sent from the event handler (_on_card_drawn).
        else:
            await client.send(mk_error(result.error, "action_failed"))

    async def _handle_play_card(
        self, client: ConnectedClient, payload: PlayCardPayload
    ) -> None:
        if not self._game:
            await client.send(mk_error("Game not started.", "game_not_started"))
            return

        result = self._game.action_play_card(
            player_id=client.player_id,
            card_uid=payload.card_uid,
            target_id=payload.target_id,
        )
        if result.ok:
            await client.send(mk_action_ack("play_card"))
        else:
            await client.send(mk_error(result.error, "action_failed"))

    async def _handle_discard_card(
        self, client: ConnectedClient, payload: DiscardCardPayload
    ) -> None:
        if not self._game:
            await client.send(mk_error("Game not started.", "game_not_started"))
            return

        result = self._game.action_discard_card(client.player_id, payload.card_uid)
        if result.ok:
            await client.send(mk_action_ack("discard_card"))
        else:
            await client.send(mk_error(result.error, "action_failed"))

    async def _handle_start_game(
        self, client: ConnectedClient, payload: StartGamePayload
    ) -> None:
        if client.player_id != self._host_player_id:
            await client.send(mk_error("Only the host can start the game.", "not_host"))
            return
        if self._deck_factory is None:
            await client.send(mk_error("Server has no deck configured.", "no_deck"))
            return
        if self.phase != RoomPhase.LOBBY:
            await client.send(mk_error("Game already started.", "already_started"))
            return
        # start_game() reports its own failures (e.g. not enough players).
        if await self.start_game(self._deck_factory()):
            await client.send(mk_action_ack("start_game"))

    async def _handle_end_turn(
        self, client: ConnectedClient, payload: EndTurnPayload
    ) -> None:
        if not self._game:
            await client.send(mk_error("Game not started.", "game_not_started"))
            return

        result = self._game.action_end_turn(client.player_id)
        if result.ok:
            await client.send(mk_action_ack("end_turn"))
        else:
            await client.send(mk_error(result.error, "action_failed"))

    async def _handle_request_snapshot(
        self, client: ConnectedClient, payload: RequestSnapshotPayload
    ) -> None:
        await self._send_snapshot_to(client)

    async def _handle_chat(
        self, client: ConnectedClient, payload: ChatPayload
    ) -> None:
        packet = mk_chat(
            sender_id=client.player_id,
            display_name=client.display_name,
            message=payload.message,
            timestamp=time.time(),
        )
        await self._broadcast(packet)

    # ------------------------------------------------------------------
    # EventBus subscriptions — engine events → client packets
    # ------------------------------------------------------------------

    def _subscribe_to_bus(self) -> None:
        """
        Wire all engine EventTypes to their forwarding handlers.

        WHY SUBSCRIBE_ALL IS NOT USED:
          Most events are just broadcast as game_event packets, but a few
          need extra logic (private hand delivery, targeted routing).
          Explicit subscriptions make the special cases self-documenting.
        """
        # Events that go to every client unchanged.
        _broadcast_types = [
            EventType.GAME_STARTED,
            EventType.GAME_ENDED,
            EventType.ROUND_STARTED,
            EventType.TURN_STARTED,
            EventType.TURN_ENDED,
            EventType.PHASE_CHANGED,
            EventType.CARD_PLAYED,
            EventType.CARD_DISCARDED,
            EventType.DAMAGE_DEALT,
            EventType.PLAYER_HEALED,
            EventType.PLAYER_ELIMINATED,
            EventType.REACTION_RESOLVED,
        ]
        for event_type in _broadcast_types:
            self._bus.subscribe(event_type, self._make_broadcast_handler())

        # Events needing extra private-data delivery.
        self._bus.subscribe(EventType.CARDS_DEALT,     self._on_cards_dealt)
        self._bus.subscribe(EventType.CARD_DRAWN,      self._on_card_drawn)
        self._bus.subscribe(EventType.STATE_SNAPSHOT,  self._on_state_snapshot)

        # Targeted events.
        self._bus.subscribe(EventType.REACTION_REQUESTED, self._on_reaction_requested)
        self._bus.subscribe(EventType.ERROR,               self._on_engine_error)

    def _make_broadcast_handler(self) -> Callable[[GameEvent], None]:
        """
        Return a closure that broadcasts any event as a game_event packet.

        WHY A FACTORY:
          All broadcast events share identical logic.  One closure per
          event type avoids 12 near-identical one-liner methods.
        """
        def handler(event: GameEvent) -> None:
            asyncio.create_task(
                self._broadcast(mk_game_event(event.type.value, event.to_dict()))
            )
        return handler

    def _on_cards_dealt(self, event: CardsDealtEvent) -> None:
        """Broadcast the public event, then send each player their private hand."""
        asyncio.create_task(self._handle_cards_dealt(event))

    async def _handle_cards_dealt(self, event: CardsDealtEvent) -> None:
        await self._broadcast(mk_game_event(event.type.value, event.to_dict()))
        if self._state is None:
            return
        for player_id, client in self._clients.items():
            if client.is_connected:
                try:
                    player = self._state.get_player(player_id)
                    await client.send(mk_private_hand(player.to_private_dict()))
                except KeyError:
                    pass

    def _on_card_drawn(self, event: CardDrawnEvent) -> None:
        """
        Broadcast the public draw count, then update the drawing player's hand.

        This fires for deck draws, steal effects, and bounties — any time
        a player gains cards.  The player_id on the event identifies who.
        """
        asyncio.create_task(self._handle_card_drawn(event))

    async def _handle_card_drawn(self, event: CardDrawnEvent) -> None:
        await self._broadcast(mk_game_event(event.type.value, event.to_dict()))
        if self._state is None:
            return
        client = self._clients.get(event.player_id)
        if client and client.is_connected:
            try:
                player = self._state.get_player(event.player_id)
                await client.send(mk_private_hand(player.to_private_dict()))
            except KeyError:
                pass

    def _on_state_snapshot(self, event: StateSnapshotEvent) -> None:
        """
        Engine emitted a snapshot (via action_request_snapshot).
        Send each client the public state + their own private hand.
        """
        asyncio.create_task(self._handle_state_snapshot(event))

    async def _handle_state_snapshot(self, event: StateSnapshotEvent) -> None:
        if self._state is None:
            return
        for player_id, client in self._clients.items():
            if client.is_connected:
                await self._send_snapshot_to(client)

    def _on_reaction_requested(self, event: ReactionRequestedEvent) -> None:
        """Send reaction window only to the players who need to respond."""
        asyncio.create_task(self._handle_reaction_requested(event))

    async def _handle_reaction_requested(self, event: ReactionRequestedEvent) -> None:
        packet = mk_game_event(event.type.value, event.to_dict())
        for player_id in event.reacting_player_ids:
            client = self._clients.get(player_id)
            if client and client.is_connected:
                await client.send(packet)

    def _on_engine_error(self, event: ErrorEvent) -> None:
        """Route engine-emitted errors to the named player only."""
        asyncio.create_task(self._handle_engine_error(event))

    async def _handle_engine_error(self, event: ErrorEvent) -> None:
        client = self._clients.get(event.player_id)
        if client and client.is_connected:
            await client.send(mk_error(event.message, event.code))

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _broadcast(
        self,
        packet: dict,
        exclude: str | None = None,
    ) -> None:
        """
        Send a packet to all connected clients, optionally skipping one.

        All sends are fire-and-forget via client.send() (queued, non-blocking).
        """
        for player_id, client in self._clients.items():
            if player_id == exclude:
                continue
            if client.is_connected:
                await client.send(packet)

    async def _broadcast_room_state(self) -> None:
        """Broadcast current lobby roster to all connected clients."""
        packet = mk_room_state(
            room_id=self.room_id,
            host_player_id=self._host_player_id or "",
            players=[
                {
                    "player_id": c.player_id,
                    "display_name": c.display_name,
                    "connected": c.is_connected,
                }
                for c in self._clients.values()
            ],
            game_phase=self.phase,
            max_players=MAX_PLAYERS,
        )
        await self._broadcast(packet)

    async def _send_snapshot_to(self, client: ConnectedClient) -> None:
        """
        Send a full state snapshot to one client.

        The public state goes to everyone; this client also gets their
        own private hand merged in.  This is the only correct place for
        that merge — nowhere else may read another player's hand.
        """
        if self._state is None:
            # Game not started yet — send lobby state instead.
            await self._broadcast_room_state()
            return

        public_state = self._state.to_public_dict()
        try:
            player = self._state.get_player(client.player_id)
            private_player = player.to_private_dict()
        except KeyError:
            private_player = {}

        await client.send(mk_state_snapshot(public_state, private_player))

    # ------------------------------------------------------------------
    # Handler registry (built once; avoids repeated dict construction)
    # ------------------------------------------------------------------

    @property
    def _message_handlers(self) -> dict[str, Callable]:
        """
        Lazy-built dispatch table mapping message type → handler coroutine.

        WHY A PROPERTY NOT A CLASS ATTRIBUTE:
          Handlers are bound methods — they can't be set at class definition
          time.  Building in __init__ would clutter it.  A cached property
          pattern keeps it clean.
        """
        if not hasattr(self, "_handlers_cache"):
            self._handlers_cache: dict[str, Callable] = {
                "draw_cards":       self._handle_draw_cards,
                "play_card":        self._handle_play_card,
                "discard_card":     self._handle_discard_card,
                "end_turn":         self._handle_end_turn,
                "start_game":       self._handle_start_game,
                "request_snapshot": self._handle_request_snapshot,
                "chat":             self._handle_chat,
            }
        return self._handlers_cache

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Public summary for HTTP lobby listing endpoint."""
        return {
            "room_id": self.room_id,
            "phase": self.phase,
            "player_count": self.player_count,
            "max_players": MAX_PLAYERS,
            "players": [
                {
                    "player_id": c.player_id,
                    "display_name": c.display_name,
                    "connected": c.is_connected,
                }
                for c in self._clients.values()
            ],
        }

    def __repr__(self) -> str:
        return (
            f"Room(id={self.room_id!r}, phase={self.phase}, "
            f"players={self.player_count})"
        )
