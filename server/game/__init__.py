"""
server/game/__init__.py

Public API surface of the game engine package.

Only import from here in external code (networking, tests, API layer).
Internal modules import from each other directly to avoid circular imports.

WHY AN EXPLICIT __init__.py:
  - Consumers shouldn't need to know the internal file layout.
  - If we ever reorganise internals (e.g. split game.py into submodules),
    the public imports here stay stable — external code doesn't break.
  - It documents exactly what the engine exposes.
"""

from game.enums import (
    CardRank,
    CardSuit,
    CardType,
    EffectType,
    EventType,
    GamePhase,
    PlayerStatus,
    Role,
    TurnPhase,
)

from game.card import (
    Card,
    CardCatalogue,
    CardDefinition,
    CardEffect,
)

from game.deck import Deck

from game.player import Player

from game.state import GameState, TurnState

from game.events import (
    # Bus
    EventBus,
    # Base
    GameEvent,
    # Lifecycle
    GameStartedEvent,
    GameEndedEvent,
    RoundStartedEvent,
    # Turn flow
    TurnStartedEvent,
    TurnEndedEvent,
    PhaseChangedEvent,
    # Cards
    CardPlayedEvent,
    CardDrawnEvent,
    CardDiscardedEvent,
    CardsDealtEvent,
    # Combat
    DamageDealtEvent,
    PlayerHealedEvent,
    PlayerEliminatedEvent,
    # Reactions
    ReactionRequestedEvent,
    ReactionResolvedEvent,
    # Sync
    StateSnapshotEvent,
    ErrorEvent,
)

from game.game import (
    ActionResult,
    EffectContext,
    Game,
)

__all__ = [
    # Enums
    "CardRank", "CardSuit", "CardType", "EffectType", "EventType",
    "GamePhase", "PlayerStatus", "Role", "TurnPhase",
    # Cards
    "Card", "CardCatalogue", "CardDefinition", "CardEffect",
    # Deck
    "Deck",
    # Player
    "Player",
    # State
    "GameState", "TurnState",
    # Events
    "EventBus", "GameEvent",
    "GameStartedEvent", "GameEndedEvent", "RoundStartedEvent",
    "TurnStartedEvent", "TurnEndedEvent", "PhaseChangedEvent",
    "CardPlayedEvent", "CardDrawnEvent", "CardDiscardedEvent", "CardsDealtEvent",
    "DamageDealtEvent", "PlayerHealedEvent", "PlayerEliminatedEvent",
    "ReactionRequestedEvent", "ReactionResolvedEvent",
    "StateSnapshotEvent", "ErrorEvent",
    # Engine
    "ActionResult", "EffectContext", "Game",
]
