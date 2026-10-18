"""
server/game/enums.py

All shared enumerations for the game engine.

WHY A SEPARATE ENUMS FILE:
  Enums are referenced across every subsystem (state, cards, events, networking).
  Centralising them here avoids circular imports and gives one authoritative place
  to add new values as the game evolves.
"""

from enum import Enum, auto


# ---------------------------------------------------------------------------
# Player / Role
# ---------------------------------------------------------------------------

class Role(str, Enum):
    """
    Player roles in the base Bang! ruleset.
    Using str-Enum so values serialise cleanly to JSON without extra work.
    """
    SHERIFF    = "sheriff"
    DEPUTY     = "deputy"
    OUTLAW     = "outlaw"
    RENEGADE   = "renegade"


class PlayerStatus(str, Enum):
    """Lifecycle of a player within a game session."""
    WAITING   = "waiting"    # lobby / not yet started
    ALIVE     = "alive"
    DEAD      = "dead"
    SPECTATOR = "spectator"


# ---------------------------------------------------------------------------
# Turn / Phase
# ---------------------------------------------------------------------------

class TurnPhase(str, Enum):
    """
    Ordered phases within a single player's turn.

    WHY EXPLICIT PHASES:
      Bang! (and variants) have strict ordering: draw → play → discard.
      Encoding this as an enum lets the engine enforce legal transitions and
      makes it trivial for game-mode subclasses to add phases (e.g. 'react').
    """
    DRAW    = "draw"
    PLAY    = "play"
    DISCARD = "discard"
    END     = "end"


class GamePhase(str, Enum):
    """Top-level lifecycle of a game room."""
    LOBBY      = "lobby"
    STARTING   = "starting"
    IN_PROGRESS = "in_progress"
    FINISHED   = "finished"


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

class CardSuit(str, Enum):
    SPADES   = "spades"
    HEARTS   = "hearts"
    DIAMONDS = "diamonds"
    CLUBS    = "clubs"


class CardRank(str, Enum):
    TWO   = "2"
    THREE = "3"
    FOUR  = "4"
    FIVE  = "5"
    SIX   = "6"
    SEVEN = "7"
    EIGHT = "8"
    NINE  = "9"
    TEN   = "10"
    JACK  = "J"
    QUEEN = "Q"
    KING  = "K"
    ACE   = "A"


class CardType(str, Enum):
    """
    High-level card classification.

    BROWN:    Single-use, played from hand, discarded immediately.
    BLUE:     Permanent equipment played in front of the player.
    GREEN:    Delayed effect (e.g. resolves at start of your turn). -- future
    CHARACTER: Character ability cards. -- future
    """
    BROWN     = "brown"
    BLUE      = "blue"
    GREEN     = "green"      # TODO: implement in a future game mode
    CHARACTER = "character"  # TODO: character system


# ---------------------------------------------------------------------------
# Effects  (used by the data-driven card system)
# ---------------------------------------------------------------------------

class EffectType(str, Enum):
    """
    Primitive effect verbs understood by the EffectResolver.

    WHY EFFECT VERBS:
      Instead of each card being a class with bespoke logic, every card is
      composed of one or more EffectType entries loaded from JSON.  The
      EffectResolver maps each verb to a handler function.  New verbs = new
      handler, no new class.
    """
    DEAL_DAMAGE          = "deal_damage"
    HEAL                 = "heal"
    DRAW_CARDS           = "draw_cards"
    DISCARD_CARD         = "discard_card"
    STEAL_CARD           = "steal_card"
    CANCEL_CARD          = "cancel_card"
    FORCE_DISCARD        = "force_discard"
    SET_RANGE_BONUS      = "set_range_bonus"      # blue card: e.g. Mustang
    SET_ATTACK_RANGE     = "set_attack_range"     # blue card: e.g. Scope
    SKIP_DRAW_PHASE      = "skip_draw_phase"      # blue card: e.g. Jail
    DEAL_DAMAGE_ALL      = "deal_damage_all"      # e.g. Gatling
    DRAW_FROM_PLAYER     = "draw_from_player"     # e.g. Panic
    NEGATE_DAMAGE        = "negate_damage"        # e.g. Missed
    EXTRA_CARD_PLAY      = "extra_card_play"      # TODO: future
    CHANGE_TURN_ORDER    = "change_turn_order"    # TODO: future


# ---------------------------------------------------------------------------
# Events  (thin mirror of events.py — kept here so enums stay self-contained)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """
    All event names emitted by the game engine.

    WHY MIRROR EVENTS AS AN ENUM:
      Networking and frontend layers subscribe by event name.  Having them as
      an enum prevents typos and makes refactoring safe.
    """
    # Game lifecycle
    GAME_STARTED        = "game_started"
    GAME_ENDED          = "game_ended"
    ROUND_STARTED       = "round_started"

    # Turn flow
    TURN_STARTED        = "turn_started"
    TURN_ENDED          = "turn_ended"
    PHASE_CHANGED       = "phase_changed"

    # Player actions
    CARD_PLAYED         = "card_played"
    CARD_DRAWN          = "card_drawn"
    CARD_DISCARDED      = "card_discarded"
    CARDS_DEALT         = "cards_dealt"

    # Combat / effects
    DAMAGE_DEALT        = "damage_dealt"
    PLAYER_HEALED       = "player_healed"
    PLAYER_ELIMINATED   = "player_eliminated"

    # Reactions / interrupts  (future)
    REACTION_REQUESTED  = "reaction_requested"
    REACTION_RESOLVED   = "reaction_resolved"

    # State sync
    STATE_SNAPSHOT      = "state_snapshot"
    ERROR               = "error"
