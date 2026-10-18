"""
server/game/card.py

Card dataclasses and the effect composition model.

DESIGN PHILOSOPHY — DATA-DRIVEN CARDS:
  In Bang! there are ~40 card types.  A naive approach creates 40 classes in
  an inheritance tree.  Instead, every card is a plain CardDefinition loaded
  from JSON, composed of a list of CardEffect descriptors.  The EffectResolver
  (in game/effects.py, TODO) maps effect types to handler functions.

  Benefits:
    - Adding a new card = adding a JSON entry, not a new class.
    - Modders can ship card packs as JSON files.
    - Easy to unit-test card effects in isolation.
    - No deep inheritance chains to navigate.

  The Card class represents a physical card instance in play (has a unique id).
  CardDefinition is the shared template (loaded once at startup).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from game.enums import CardSuit, CardRank, CardType, EffectType


# ---------------------------------------------------------------------------
# Effect descriptor  (the atom of the card system)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardEffect:
    """
    A single composable effect attached to a card.

    WHY FROZEN:
      CardEffects are part of CardDefinitions which are loaded once and shared
      across all card instances.  Making them immutable prevents accidental
      mutation during gameplay.

    'params' holds effect-specific configuration loaded from JSON, e.g.:
      { "amount": 1 }          for DEAL_DAMAGE
      { "count": 2 }           for DRAW_CARDS
      { "range_bonus": 1 }     for SET_RANGE_BONUS

    The EffectResolver reads these params when applying the effect.
    """
    type: EffectType
    params: dict[str, Any] = field(default_factory=dict)

    # TODO: add 'condition' field for conditional effects (e.g. "if target
    #       is within range 1") when the condition system is designed.


# ---------------------------------------------------------------------------
# Card definition  (the shared template, loaded from JSON)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardDefinition:
    """
    Immutable template for a card type.  Loaded from cards/*.json at startup.

    One CardDefinition is shared by all Card instances of the same type.
    Think of it as the 'class' and Card as the 'instance'.

    Fields:
        id          Unique string identifier, e.g. "bang", "missed", "beer".
                    Matches the JSON definition key.
        name        Display name (can be localised by swapping name tables).
        card_type   BROWN / BLUE / GREEN / CHARACTER.
        effects     Ordered list of effects applied when the card is played.
        on_equip    Effects applied when a BLUE card enters play.
        on_unequip  Effects applied when a BLUE card leaves play (death / steal).
        tags        Free-form tags for game-mode filtering, e.g. ["weapon"].
        max_range   For attack cards: maximum target range (None = unlimited).
        min_range   For attack cards: minimum target range.
    """
    id: str
    name: str
    card_type: CardType
    effects: tuple[CardEffect, ...] = field(default_factory=tuple)
    on_equip: tuple[CardEffect, ...] = field(default_factory=tuple)
    on_unequip: tuple[CardEffect, ...] = field(default_factory=tuple)
    tags: frozenset[str] = field(default_factory=frozenset)
    max_range: int | None = None
    min_range: int = 1
    description: str = ""           # human-readable flavour / tooltip text

    # TODO: add 'reaction_to' field: frozenset[EffectType] that this card can
    #       interrupt when the reaction system is implemented.

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags

    def __repr__(self) -> str:
        return f"CardDefinition(id={self.id!r}, type={self.card_type})"


# ---------------------------------------------------------------------------
# Card instance  (a physical card in the game)
# ---------------------------------------------------------------------------

@dataclass
class Card:
    """
    A single physical card in play.

    Each Card has a unique instance id (uid) so the engine can track exactly
    which copy moved where — important when the same definition appears
    multiple times in a deck (e.g. four Bang! cards).

    The definition holds all rules; the instance holds only location metadata.

    Usage::

        definition = CardDefinition(
            id="bang",
            name="Bang!",
            card_type=CardType.BROWN,
            effects=(CardEffect(type=EffectType.DEAL_DAMAGE, params={"amount": 1}),),
        )
        card = Card.from_definition(definition, suit=CardSuit.SPADES, rank=CardRank.ACE)
        print(card.uid)   # "bang-<uuid4>"
    """
    uid: str                     # globally unique instance id
    definition: CardDefinition
    suit: CardSuit
    rank: CardRank
    owner_id: str | None = None  # player_id of current owner, or None if in deck

    # --- convenience pass-throughs ---

    @property
    def id(self) -> str:
        """Definition id shortcut."""
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def card_type(self) -> CardType:
        return self.definition.card_type

    @property
    def effects(self) -> tuple[CardEffect, ...]:
        return self.definition.effects

    @property
    def tags(self) -> frozenset[str]:
        return self.definition.tags

    @property
    def max_range(self) -> "int | None":
        return self.definition.max_range

    @property
    def min_range(self) -> int:
        return self.definition.min_range

    # --- factory ---

    @classmethod
    def from_definition(
        cls,
        definition: CardDefinition,
        suit: CardSuit,
        rank: CardRank,
    ) -> "Card":
        """Create a new Card instance from a definition."""
        uid = f"{definition.id}-{uuid.uuid4().hex[:8]}"
        return cls(uid=uid, definition=definition, suit=suit, rank=rank)

    def __repr__(self) -> str:
        return (
            f"Card(uid={self.uid!r}, name={self.name!r}, "
            f"suit={self.suit}, rank={self.rank})"
        )


# ---------------------------------------------------------------------------
# Card catalogue  (in-memory registry of all loaded definitions)
# ---------------------------------------------------------------------------

class CardCatalogue:
    """
    Registry of all CardDefinitions available in the current game session.

    WHY A CATALOGUE CLASS:
      A global dict would work, but the class lets us inject different
      catalogues per game mode (e.g. base game vs expansion) and makes
      dependency injection in tests trivial.

    Populated by the CardLoader (game/card_loader.py) at startup.

    Usage::

        catalogue = CardCatalogue()
        catalogue.register(bang_definition)
        defn = catalogue.get("bang")
    """

    def __init__(self) -> None:
        self._definitions: dict[str, CardDefinition] = {}

    def register(self, definition: CardDefinition) -> None:
        """Add a definition; raises ValueError on duplicate id."""
        if definition.id in self._definitions:
            raise ValueError(
                f"CardDefinition '{definition.id}' is already registered."
            )
        self._definitions[definition.id] = definition

    def get(self, card_id: str) -> CardDefinition:
        """Retrieve a definition by id; raises KeyError if missing."""
        try:
            return self._definitions[card_id]
        except KeyError:
            raise KeyError(f"Unknown card definition: {card_id!r}")

    def all(self) -> list[CardDefinition]:
        return list(self._definitions.values())

    def __len__(self) -> int:
        return len(self._definitions)

    def __repr__(self) -> str:
        return f"CardCatalogue({len(self)} definitions)"
