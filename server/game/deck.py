"""
server/game/deck.py

Deck and discard pile management.

DESIGN NOTES:
  - Deck owns the draw pile and discard pile together — they are always paired.
  - Deterministic shuffle via a seeded Random instance.  The seed is stored in
    GameState so games can be replayed exactly from the same seed.
  - No game logic lives here — only card movement primitives.
  - The engine never touches self._cards directly outside this module.

WHY DETERMINISTIC RNG:
  Server-authoritative randomness must be reproducible for:
    1. Replay / audit logs.
    2. Testing specific shuffle scenarios.
    3. Debugging reported bugs with exact game state.
  Pass a fixed seed in tests; omit it in production for a random seed.
"""

from __future__ import annotations

import random
import logging
from dataclasses import dataclass, field

from game.card import Card

logger = logging.getLogger(__name__)


@dataclass
class Deck:
    """
    Manages the draw pile and discard pile for a game session.

    The two piles are kept together because the discard pile is reshuffled
    into the draw pile when the draw pile is exhausted.

    Usage::

        deck = Deck(seed=42)
        deck.load(cards)          # load a list of Card instances
        deck.shuffle()
        card = deck.draw()
        deck.discard(card)
        deck.reshuffle_discard()  # called automatically by draw() if needed
    """

    seed: int | None = None

    # Internal piles — use the public API, not these directly.
    _draw_pile: list[Card] = field(default_factory=list, repr=False)
    _discard_pile: list[Card] = field(default_factory=list, repr=False)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Each Deck gets its own Random instance so multiple decks in a
        # session (unlikely, but possible in multi-game servers) don't share
        # global RNG state.
        self._rng = random.Random(self.seed)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def load(self, cards: list[Card]) -> None:
        """
        Populate the draw pile.  Call before shuffle().
        Replaces any existing cards — intended for initial setup only.
        """
        self._draw_pile = list(cards)
        self._discard_pile = []

    def shuffle(self) -> None:
        """Shuffle the draw pile in place using the seeded RNG."""
        self._rng.shuffle(self._draw_pile)
        logger.debug("Deck shuffled, %d cards in draw pile.", len(self._draw_pile))

    # ------------------------------------------------------------------
    # Draw / discard
    # ------------------------------------------------------------------

    def draw(self, *, auto_reshuffle: bool = True) -> Card | None:
        """
        Draw the top card from the draw pile.

        If the draw pile is empty and auto_reshuffle is True, the discard
        pile is reshuffled into the draw pile first (standard Bang! rule).

        Returns None if both piles are empty (edge case in short games).
        """
        if not self._draw_pile:
            if auto_reshuffle and self._discard_pile:
                self.reshuffle_discard()
            else:
                logger.warning("Draw attempted on an empty deck.")
                return None

        card = self._draw_pile.pop()
        card.owner_id = None
        return card

    def draw_many(self, count: int, *, auto_reshuffle: bool = True) -> list[Card]:
        """Draw multiple cards at once.  Returns fewer than count if deck runs dry."""
        drawn: list[Card] = []
        for _ in range(count):
            card = self.draw(auto_reshuffle=auto_reshuffle)
            if card is None:
                break
            drawn.append(card)
        return drawn

    def discard(self, card: Card) -> None:
        """Place a card face-up on the discard pile."""
        card.owner_id = None
        self._discard_pile.append(card)
        logger.debug("Card discarded: %s", card)

    def discard_many(self, cards: list[Card]) -> None:
        for card in cards:
            self.discard(card)

    # ------------------------------------------------------------------
    # Reshuffle
    # ------------------------------------------------------------------

    def reshuffle_discard(self) -> None:
        """
        Move all discard pile cards to the draw pile and shuffle.

        WHY KEEP TOP DISCARD:
          Standard Bang! rule: the top discard card stays visible.
          We preserve that by keeping the last discarded card in place.
          TODO: make this behaviour configurable per game mode.
        """
        if not self._discard_pile:
            return

        top_card = self._discard_pile.pop()        # keep top card visible
        self._draw_pile = self._discard_pile[:]
        self._discard_pile = [top_card]
        self._rng.shuffle(self._draw_pile)
        logger.info(
            "Discard reshuffled into draw pile. %d cards available.",
            len(self._draw_pile),
        )

    # ------------------------------------------------------------------
    # Peek / inspection (used by special card effects)
    # ------------------------------------------------------------------

    def peek_top(self, count: int = 1) -> list[Card]:
        """
        Look at the top N cards of the draw pile without removing them.
        Used by 'Lucky Duke' ability and similar effects.
        Returns cards in draw order (last element = next to be drawn).
        """
        return self._draw_pile[-count:]

    @property
    def top_discard(self) -> Card | None:
        """The face-up top card of the discard pile, or None."""
        return self._discard_pile[-1] if self._discard_pile else None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def draw_count(self) -> int:
        return len(self._draw_pile)

    @property
    def discard_count(self) -> int:
        return len(self._discard_pile)

    @property
    def total_cards(self) -> int:
        return self.draw_count + self.discard_count

    def is_empty(self) -> bool:
        return self.draw_count == 0 and self.discard_count == 0

    # ------------------------------------------------------------------
    # State export  (for snapshots / networking)
    # ------------------------------------------------------------------

    def to_public_dict(self) -> dict:
        """
        Public state safe to broadcast to all clients.
        Card details in the draw pile are hidden; only counts are shared.
        """
        return {
            "draw_count": self.draw_count,
            "discard_count": self.discard_count,
            "top_discard": {
                "id": self.top_discard.id,
                "name": self.top_discard.name,
                "suit": self.top_discard.suit.value,
                "rank": self.top_discard.rank.value,
            } if self.top_discard else None,
        }

    def __repr__(self) -> str:
        return (
            f"Deck(draw={self.draw_count}, discard={self.discard_count}, "
            f"seed={self.seed})"
        )
