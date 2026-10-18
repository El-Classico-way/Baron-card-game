"""
server/game/player.py

Player state for a game session.

DESIGN NOTES:
  Player is a plain data container + state-mutation methods.
  It knows nothing about networking, rendering, or game rules.

  WHY NOT A PYDANTIC MODEL:
    Player state mutates frequently during a game. Pydantic's immutability
    and validation overhead on every mutation is wasteful here. We validate
    at the network boundary (incoming actions), not inside the engine.

  HAND PRIVACY:
    The hand is private information. to_public_dict() deliberately omits
    card details. The networking layer calls to_private_dict() and sends it
    only to the owning client.

  EQUIPMENT:
    Blue cards played in front of a player are stored in `equipment` keyed by
    card uid. At most one weapon may be equipped at a time (enforced by the
    engine, not here — Player doesn't know game rules).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from game.card import Card
from game.enums import PlayerStatus, Role

logger = logging.getLogger(__name__)


@dataclass
class Player:
    """
    Represents one player's complete in-game state.

    Fields set at construction:
        player_id       Stable identifier matching the session/user id.
        display_name    Human-readable name for UI.
        max_hp          Maximum hit points (varies by role: Sheriff gets +1).

    Fields managed during gameplay:
        hp              Current hit points.
        hand            Cards currently held in hand (private).
        equipment       Blue cards in play in front of this player.
        role            Assigned role (hidden until death or game end).
        status          ALIVE / DEAD / SPECTATOR.
        has_played_bang Whether this player has played a Bang! this turn
                        (enforced by the turn engine, tracked here).

    Usage::

        player = Player(player_id="abc123", display_name="Alice", max_hp=4)
        player.deal(card1)
        player.deal(card2)
        player.take_damage(1)
        player.play_card(card1.uid)   # removes from hand, returns Card
    """

    player_id: str
    display_name: str
    max_hp: int

    # Runtime state
    hp: int = field(init=False)
    hand: list[Card] = field(default_factory=list, repr=False)
    equipment: dict[str, Card] = field(default_factory=dict, repr=False)   # uid → Card
    role: Role | None = None
    status: PlayerStatus = PlayerStatus.WAITING
    has_played_bang: bool = False        # reset each turn

    # Turn tracking
    cards_drawn_this_turn: int = 0       # reset each draw phase
    cards_played_this_turn: int = 0      # reset each turn

    def __post_init__(self) -> None:
        self.hp = self.max_hp

    # ------------------------------------------------------------------
    # Hand management
    # ------------------------------------------------------------------

    def deal(self, card: Card) -> None:
        """Add a card to this player's hand."""
        card.owner_id = self.player_id
        self.hand.append(card)

    def deal_many(self, cards: list[Card]) -> None:
        for card in cards:
            self.deal(card)

    def play_card(self, card_uid: str) -> Card:
        """
        Remove a card from hand and return it (played to the table).
        Raises ValueError if the card is not in hand.
        """
        card = self._find_in_hand(card_uid)
        self.hand.remove(card)
        self.cards_played_this_turn += 1
        logger.debug(
            "Player %s played card %s (%s).",
            self.player_id, card_uid, card.name,
        )
        return card

    def discard_card(self, card_uid: str) -> Card:
        """
        Remove a card from hand for discarding (end-of-turn excess cards).
        Raises ValueError if the card is not in hand.
        """
        card = self._find_in_hand(card_uid)
        self.hand.remove(card)
        return card

    def lose_card_from_hand(self, card_uid: str) -> Card:
        """
        Remove a card from hand due to an opponent's effect (e.g. Cat Balou).
        Same mechanically as discard_card, named separately for clarity.
        """
        return self.discard_card(card_uid)

    def _find_in_hand(self, card_uid: str) -> Card:
        for card in self.hand:
            if card.uid == card_uid:
                return card
        raise ValueError(
            f"Card {card_uid!r} not found in {self.player_id}'s hand."
        )

    def has_card_in_hand(self, card_uid: str) -> bool:
        return any(c.uid == card_uid for c in self.hand)

    def hand_count(self) -> int:
        return len(self.hand)

    # ------------------------------------------------------------------
    # Equipment (blue cards)
    # ------------------------------------------------------------------

    def equip(self, card: Card) -> None:
        """
        Place a blue card into this player's equipment area.
        Raises ValueError if a card with the same definition id is already
        equipped (you can't have two Mustangs, etc.).
        """
        if any(c.definition.id == card.definition.id for c in self.equipment.values()):
            raise ValueError(
                f"Player {self.player_id} already has '{card.name}' equipped."
            )
        card.owner_id = self.player_id
        self.equipment[card.uid] = card

    def unequip(self, card_uid: str) -> Card:
        """Remove and return an equipped card. Raises KeyError if not found."""
        card = self.equipment.pop(card_uid)
        card.owner_id = None
        return card

    def has_equipment(self, definition_id: str) -> bool:
        """Check if a card definition is equipped (e.g. 'mustang')."""
        return any(
            c.definition.id == definition_id
            for c in self.equipment.values()
        )

    def equipped_weapon(self) -> Card | None:
        """Return the currently equipped weapon card, or None."""
        for card in self.equipment.values():
            if card.definition.has_tag("weapon"):
                return card
        return None

    # ------------------------------------------------------------------
    # HP management
    # ------------------------------------------------------------------

    def take_damage(self, amount: int = 1) -> int:
        """
        Reduce HP. Returns the new HP value.
        Caller (engine) is responsible for checking if player is eliminated.
        """
        self.hp = max(0, self.hp - amount)
        logger.debug(
            "Player %s took %d damage, now at %d/%d HP.",
            self.player_id, amount, self.hp, self.max_hp,
        )
        return self.hp

    def heal(self, amount: int = 1) -> int:
        """
        Increase HP up to max_hp. Returns amount actually healed.
        """
        before = self.hp
        self.hp = min(self.max_hp, self.hp + amount)
        healed = self.hp - before
        logger.debug(
            "Player %s healed %d HP (%d → %d).",
            self.player_id, healed, before, self.hp,
        )
        return healed

    @property
    def is_alive(self) -> bool:
        return self.status == PlayerStatus.ALIVE

    @property
    def is_dead(self) -> bool:
        return self.status == PlayerStatus.DEAD

    def eliminate(self, eliminated_by: str | None = None) -> None:
        """Mark this player as eliminated."""
        self.status = PlayerStatus.DEAD
        self.hp = 0
        logger.info(
            "Player %s eliminated by %s.", self.player_id, eliminated_by
        )

    # ------------------------------------------------------------------
    # Range helpers  (pure query — no game logic)
    # ------------------------------------------------------------------

    @property
    def defense_range_bonus(self) -> int:
        """
        Extra range opponents must cover to target this player.
        Comes from equipped cards like Mustang (+1).
        """
        bonus = 0
        for card in self.equipment.values():
            for effect in card.definition.on_equip:
                if effect.type.value == "set_range_bonus":
                    bonus += effect.params.get("range_bonus", 0)
        return bonus

    @property
    def attack_range_bonus(self) -> int:
        """
        Extra attack range from equipped cards like Scope (+1) or weapons.
        """
        bonus = 0
        for card in self.equipment.values():
            for effect in card.definition.on_equip:
                if effect.type.value == "set_attack_range":
                    bonus += effect.params.get("range_bonus", 0)
        return bonus

    # ------------------------------------------------------------------
    # Turn state reset
    # ------------------------------------------------------------------

    def reset_turn_state(self) -> None:
        """Called by the engine at the start of this player's turn."""
        self.has_played_bang = False
        self.cards_drawn_this_turn = 0
        self.cards_played_this_turn = 0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_public_dict(self) -> dict:
        """
        State visible to all players:
          - HP, status, equipment (blue cards are public)
          - Hand count only — card identities are private.
        """
        return {
            "player_id": self.player_id,
            "display_name": self.display_name,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "status": self.status.value,
            "hand_count": self.hand_count(),
            "equipment": [
                {
                    "uid": c.uid,
                    "id": c.id,
                    "name": c.name,
                    "suit": c.suit.value,
                    "rank": c.rank.value,
                }
                for c in self.equipment.values()
            ],
            # Role is hidden until death; the engine reveals it on eliminate()
            "role": self.role.value if self.role and self.is_dead else None,
        }

    def to_private_dict(self) -> dict:
        """
        Full state sent only to this player's own client.
        Includes hand card details and role.
        """
        public = self.to_public_dict()
        public["role"] = self.role.value if self.role else None
        public["hand"] = [
            {
                "uid": c.uid,
                "id": c.id,
                "name": c.name,
                "suit": c.suit.value,
                "rank": c.rank.value,
                "description": c.definition.description,
            }
            for c in self.hand
        ]
        return public

    def __repr__(self) -> str:
        return (
            f"Player(id={self.player_id!r}, name={self.display_name!r}, "
            f"hp={self.hp}/{self.max_hp}, status={self.status})"
        )
