"""
server/game/events.py

Typed event dataclasses emitted by the game engine.

WHY TYPED EVENTS (not plain dicts):
  Every consumer of engine output — the networking layer, the pygame debug
  client, replay recorders, test assertions — deserves a stable, typed
  contract.  Dataclasses give us that for free with zero runtime cost.
  They also serialise trivially to JSON via dataclasses.asdict().

WHY NOT PYDANTIC HERE:
  The game engine is intentionally pure Python with no external dependencies.
  Pydantic belongs in the networking / API boundary layer where we validate
  untrusted input.  Inside the engine we trust our own data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from game.enums import EventType


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@dataclass
class GameEvent:
    """
    Root of the event hierarchy.

    All events carry a timestamp and the event type so consumers can dispatch
    without isinstance() checks.  The 'payload' field holds event-specific
    structured data (see subclasses below).

    WHY TIMESTAMP ON EVERY EVENT:
      Replay, audit logging, and latency debugging all need ordering.
      Stamping at construction is cheap and always correct.
    """
    type: EventType
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Shallow serialisation for networking layer consumption."""
        import dataclasses
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Game lifecycle events
# ---------------------------------------------------------------------------

@dataclass
class GameStartedEvent(GameEvent):
    type: EventType = field(default=EventType.GAME_STARTED, init=False)
    player_ids: list[str] = field(default_factory=list)
    mode: str = "classic"


@dataclass
class GameEndedEvent(GameEvent):
    type: EventType = field(default=EventType.GAME_ENDED, init=False)
    winning_team: list[str] = field(default_factory=list)   # player_ids
    reason: str = ""


@dataclass
class RoundStartedEvent(GameEvent):
    type: EventType = field(default=EventType.ROUND_STARTED, init=False)
    round_number: int = 0


# ---------------------------------------------------------------------------
# Turn flow events
# ---------------------------------------------------------------------------

@dataclass
class TurnStartedEvent(GameEvent):
    type: EventType = field(default=EventType.TURN_STARTED, init=False)
    player_id: str = ""
    turn_number: int = 0


@dataclass
class TurnEndedEvent(GameEvent):
    type: EventType = field(default=EventType.TURN_ENDED, init=False)
    player_id: str = ""


@dataclass
class PhaseChangedEvent(GameEvent):
    type: EventType = field(default=EventType.PHASE_CHANGED, init=False)
    player_id: str = ""
    new_phase: str = ""


# ---------------------------------------------------------------------------
# Card events
# ---------------------------------------------------------------------------

@dataclass
class CardPlayedEvent(GameEvent):
    type: EventType = field(default=EventType.CARD_PLAYED, init=False)
    player_id: str = ""
    card_id: str = ""
    card_name: str = ""
    target_id: str | None = None     # optional target player


@dataclass
class CardDrawnEvent(GameEvent):
    type: EventType = field(default=EventType.CARD_DRAWN, init=False)
    player_id: str = ""
    count: int = 0
    source: str = "deck"             # "deck" | "discard" | "player:<id>"


@dataclass
class CardDiscardedEvent(GameEvent):
    type: EventType = field(default=EventType.CARD_DISCARDED, init=False)
    player_id: str = ""
    card_id: str = ""
    card_name: str = ""


@dataclass
class CardsDealtEvent(GameEvent):
    """Emitted once at game start when initial hands are distributed."""
    type: EventType = field(default=EventType.CARDS_DEALT, init=False)
    # Per-player card counts only — hands are private information.
    # The networking layer sends each player their own hand separately.
    player_card_counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Combat / effect events
# ---------------------------------------------------------------------------

@dataclass
class DamageDealtEvent(GameEvent):
    type: EventType = field(default=EventType.DAMAGE_DEALT, init=False)
    source_id: str | None = None     # None = environment / indirect
    target_id: str = ""
    amount: int = 1
    cause: str = ""                  # card name that caused the damage


@dataclass
class PlayerHealedEvent(GameEvent):
    type: EventType = field(default=EventType.PLAYER_HEALED, init=False)
    player_id: str = ""
    amount: int = 1
    new_hp: int = 0


@dataclass
class PlayerEliminatedEvent(GameEvent):
    type: EventType = field(default=EventType.PLAYER_ELIMINATED, init=False)
    player_id: str = ""
    eliminated_by: str | None = None
    role: str = ""                   # revealed on death


# ---------------------------------------------------------------------------
# Reaction / interrupt events  (future system hook)
# ---------------------------------------------------------------------------

@dataclass
class ReactionRequestedEvent(GameEvent):
    """
    Emitted when a card effect allows one or more players to react.

    WHY THIS EXISTS NOW (but is not fully implemented):
      The reaction/interrupt system is architecturally complex.  Defining the
      event shape now keeps the networking layer stable when we implement it.
      The engine emits this; the networking layer starts a reaction timer.
    """
    type: EventType = field(default=EventType.REACTION_REQUESTED, init=False)
    trigger_card_id: str = ""
    reacting_player_ids: list[str] = field(default_factory=list)
    timeout_seconds: float = 10.0
    # TODO: add legal_reaction_card_types when reaction system is built


@dataclass
class ReactionResolvedEvent(GameEvent):
    type: EventType = field(default=EventType.REACTION_RESOLVED, init=False)
    trigger_card_id: str = ""
    reacted: bool = False            # True if someone played a reaction
    reactor_id: str | None = None


# ---------------------------------------------------------------------------
# State sync events
# ---------------------------------------------------------------------------

@dataclass
class StateSnapshotEvent(GameEvent):
    """
    Full public state snapshot for reconnecting clients.
    The networking layer adds private hand data before forwarding to each client.
    """
    type: EventType = field(default=EventType.STATE_SNAPSHOT, init=False)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorEvent(GameEvent):
    type: EventType = field(default=EventType.ERROR, init=False)
    player_id: str = ""
    message: str = ""
    code: str = ""


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

from collections.abc import Callable, Awaitable
import asyncio
import logging

logger = logging.getLogger(__name__)

# A handler can be sync or async.
Handler = Callable[[GameEvent], None] | Callable[[GameEvent], Awaitable[None]]


class EventBus:
    """
    Lightweight pub/sub bus used by the game engine.

    WHY A SIMPLE BUS INSTEAD OF A FRAMEWORK:
      The engine emits events; networking and UI subscribe.  We don't need
      priority queues, filtering, or persistence — those belong in the layers
      above.  Keeping this simple means the engine has zero external deps.

    ASYNC SUPPORT:
      Handlers may be sync or async coroutines.  The bus calls async handlers
      with asyncio.create_task() so the engine itself does not need to be
      async (important for unit testing).

    Usage::

        bus = EventBus()

        def on_damage(event: DamageDealtEvent) -> None:
            print(f"{event.target_id} took {event.amount} damage")

        bus.subscribe(EventType.DAMAGE_DEALT, on_damage)
        bus.emit(DamageDealtEvent(target_id="player1", amount=1))
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = {}

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        """Register a handler for a specific event type."""
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        """Remove a previously registered handler."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def subscribe_all(self, handler: Handler) -> None:
        """
        Subscribe to every event type.
        Useful for logging, replay recording, and test assertions.
        """
        for event_type in EventType:
            self.subscribe(event_type, handler)

    def emit(self, event: GameEvent) -> None:
        """
        Dispatch an event to all registered handlers.

        Sync handlers are called immediately in order.
        Async handlers are scheduled as asyncio tasks.
        """
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                logger.exception(
                    "EventBus handler error for event %s", event.type
                )

    def clear(self) -> None:
        """Remove all subscriptions. Useful between test cases."""
        self._handlers.clear()
