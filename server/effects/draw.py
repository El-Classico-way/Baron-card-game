"""
server/effects/draw.py

Card-draw and card-theft effects.

EFFECTS IN THIS MODULE:
  DrawCardsEffect      Draw N cards from the deck (Stagecoach, Wells Fargo).
  StealCardEffect      Take a card from a target's hand/equipment (Panic!).
  DrawFromPlayerEffect Alias for steal — same mechanic, different flavour.

CARD SELECTION — CURRENT STATE:
  Both steal effects currently pick a random card using the game's seeded RNG.
  In the real game, the acting player *chooses* which card to take (from hand
  or from equipment).  The selection logic needs to come from the action payload
  once the networking layer passes it through.

  The hook for that is `_select_target_card()`.  Replace its body with
  actual player-choice logic without touching resolve().

CHAINING EXAMPLE — "Wells Fargo" (draw 3 cards):
  Simply set params={"count": 3}.  No new class needed.

CHAINING EXAMPLE — "Panic! + condition":
  A hypothetical "Advanced Panic" that steals a card AND deals 1 damage
  if the stolen card was a Bang! would be:

      effects: [
        {"type": "draw_from_player", "params": {"count": 1}},
        {"type": "deal_damage",      "params": {"amount": 1,
                                                 "condition": "stolen_is_bang"}}
      ]

  The condition system doesn't exist yet — mark it as TODO in the second
  effect's params and check it in DamageEffect when conditions are designed.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from effects.base import Effect, EffectResult, TargetSpec
from game.card import Card
from game.enums import EffectType
from game.events import CardDrawnEvent

if TYPE_CHECKING:
    from game.game import EffectContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DrawCardsEffect — draw from the deck (e.g. Stagecoach, Wells Fargo)
# ---------------------------------------------------------------------------

class DrawCardsEffect(Effect):
    """
    Draw N cards from the shared draw pile into the acting player's hand.

    Params:
        count     int   Number of cards to draw.   Default: 1

    Notes:
        The deck auto-reshuffles the discard pile if it runs dry (Deck.draw).
        If both piles are empty, fewer than `count` cards may be dealt — this
        is reflected in metadata["actually_drawn"].

    Usage in card JSON::

        # Stagecoach
        {"type": "draw_cards", "params": {"count": 2}}

        # Wells Fargo
        {"type": "draw_cards", "params": {"count": 3}}
    """

    effect_type = EffectType.DRAW_CARDS
    target_spec = TargetSpec()   # no target — always draws to self

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        count: int = ctx.effect.params.get("count", 1)

        drawn = ctx.state.deck.draw_many(count)

        if not drawn:
            logger.warning(
                "DrawCardsEffect: deck and discard are both empty for %s.",
                ctx.acting_player.player_id,
            )
            return EffectResult.skipped("deck empty")

        ctx.acting_player.deal_many(drawn)

        ctx.game.bus.emit(CardDrawnEvent(
            player_id=ctx.acting_player.player_id,
            count=len(drawn),
            source="deck",
        ))

        logger.debug(
            "%s drew %d card(s) (requested %d).",
            ctx.acting_player.player_id, len(drawn), count,
        )

        return EffectResult(
            applied=True,
            metadata={
                "actually_drawn": len(drawn),
                "requested": count,
                "short": len(drawn) < count,
            },
        )


# ---------------------------------------------------------------------------
# StealCardEffect — take a card from a target (e.g. Panic!)
# ---------------------------------------------------------------------------

class StealCardEffect(Effect):
    """
    Steal one card from a target player's hand or equipment.

    The stolen card goes directly into the acting player's hand.
    No event for the card leaving the target — only CardDrawnEvent for
    the acting player gaining it (consistent with game.py's existing pattern).

    Params:
        count       int   Number of cards to steal.  Default: 1
                          (Most steal effects take 1; keep room for variants.)
        from_hand   bool  Allow stealing from hand.   Default: True
        from_equip  bool  Allow stealing equipment.   Default: True

    TargetSpec:
        requires_target    = True   — must have a target
        allow_self         = False  — can't steal from yourself
        must_be_alive      = True
        require_hand_cards = 1      — target must have at least 1 card in hand
                                      (when from_hand=True; checked in resolve)

    TODO: _select_target_card() currently picks randomly.  Replace with
          player-choice logic once the networking payload carries card_uid.

    Usage in card JSON::

        # Panic!
        {"type": "steal_card", "params": {"count": 1}}
    """

    effect_type = EffectType.STEAL_CARD
    target_spec = TargetSpec(
        requires_target=True,
        allow_self=False,
        must_be_alive=True,
    )

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        err = self.validate_target(ctx)
        if err:
            return EffectResult.skipped(err)

        target = ctx.target_player
        count: int = ctx.effect.params.get("count", 1)
        from_hand: bool = ctx.effect.params.get("from_hand", True)
        from_equip: bool = ctx.effect.params.get("from_equip", True)

        # Build pool of stealable cards.
        pool: list[Card] = []
        if from_hand:
            pool.extend(target.hand)
        if from_equip:
            pool.extend(target.equipment.values())

        if not pool:
            logger.debug(
                "StealCardEffect: %s has no stealable cards.",
                target.player_id,
            )
            return EffectResult.skipped("target has no stealable cards")

        stolen_cards: list[Card] = []
        for _ in range(min(count, len(pool))):
            card = self._select_target_card(ctx, pool)
            if card is None:
                break
            self._take_card(card, target, ctx)
            ctx.acting_player.deal(card)
            stolen_cards.append(card)
            pool.remove(card)

        if not stolen_cards:
            return EffectResult.skipped("no cards could be stolen")

        ctx.game.bus.emit(CardDrawnEvent(
            player_id=ctx.acting_player.player_id,
            count=len(stolen_cards),
            source=f"player:{target.player_id}",
        ))

        logger.debug(
            "%s stole %d card(s) from %s.",
            ctx.acting_player.player_id, len(stolen_cards), target.player_id,
        )

        return EffectResult(
            applied=True,
            metadata={
                "stolen_count": len(stolen_cards),
                "stolen_ids": [c.id for c in stolen_cards],
                "from_player": target.player_id,
            },
        )

    def _select_target_card(
        self,
        ctx: "EffectContext",
        pool: list[Card],
    ) -> Card | None:
        """
        Choose which card to steal from the pool.

        TODO: Replace random selection with player-chosen card_uid from the
              action payload.  The networking layer should pass the chosen
              card_uid through EffectContext (requires extending EffectContext
              with an `action_payload: dict` field).

        Current behaviour: seeded-random pick for determinism in tests.
        """
        if not pool:
            return None
        rng = random.Random(ctx.state.seed)
        return rng.choice(pool)

    @staticmethod
    def _take_card(card: Card, target: "Any", ctx: "EffectContext") -> None:
        """Remove a card from hand or equipment, depending on where it lives."""
        if card.uid in target.equipment:
            target.unequip(card.uid)
        else:
            target.lose_card_from_hand(card.uid)


# ---------------------------------------------------------------------------
# DrawFromPlayerEffect — alias for StealCardEffect with DRAW_FROM_PLAYER type
# ---------------------------------------------------------------------------

class DrawFromPlayerEffect(StealCardEffect):
    """
    Identical to StealCardEffect but registered under DRAW_FROM_PLAYER.

    WHY A SUBCLASS AND NOT JUST RE-REGISTERING:
      Two different EffectType values need to map to the same behaviour, but
      EffectRegistry keys on effect_type.  Subclassing with only effect_type
      overridden is the cleanest way to do this without aliasing or special
      cases in the registry.

      If the behaviours ever diverge (e.g. DrawFromPlayer can only steal from
      hand, not equipment), override resolve() in this subclass only.

    Usage in card JSON::

        # A card using the draw_from_player verb (semantically "draw", not "steal")
        {"type": "draw_from_player", "params": {"count": 1, "from_equip": false}}
    """

    effect_type = EffectType.DRAW_FROM_PLAYER
