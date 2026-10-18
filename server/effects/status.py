"""
server/effects/status.py

Status and control effects: hand management, passive equipment bonuses,
turn-phase skipping, and future status conditions.

EFFECTS IN THIS MODULE:
  ForceDiscardEffect          Force target to discard a card (Cat Balou).
  DiscardCardEffect           Alias for ForceDiscard (different EffectType).
  RangePassiveEffect          Equipment: +N to opponents' effective range (Mustang).
  AttackRangePassiveEffect    Equipment: +N to this player's attack range (Scope).
  SkipDrawPhaseEffect         Equipment: target skips draw phase (Jail).

PASSIVE EFFECTS EXPLANATION:
  RangePassiveEffect and AttackRangePassiveEffect are never called during
  effect resolution — they are *passive*, read by GameState.range_between()
  through Player.defense_range_bonus and Player.attack_range_bonus.

  They are registered in the EffectRegistry so:
    a) No "no handler" warning fires when blue cards are equipped.
    b) Tests can inspect what passive effects a card carries.
    c) A future "stat sheet" UI can enumerate active bonuses.

  Their resolve() returns EffectResult.skipped("passive") immediately.

FUTURE STATUS CONDITIONS:
  The status_conditions dict on Player is a TODO placeholder for things like:
    - "poisoned": take 1 damage at end of turn
    - "jailed": skip next draw phase
    - "dynamite_attached": flip at start of turn
  These would be stored as StatusCondition dataclasses keyed by name.
  This module will grow to hold AddStatusEffect, RemoveStatusEffect, etc.
  See TODO below.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from effects.base import Effect, EffectResult, TargetSpec
from game.enums import EffectType
from game.events import CardDiscardedEvent

if TYPE_CHECKING:
    from game.game import EffectContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ForceDiscardEffect — make target discard a card (e.g. Cat Balou)
# ---------------------------------------------------------------------------

class ForceDiscardEffect(Effect):
    """
    Force a target player to discard one card from hand or equipment.

    Params:
        count       int   Cards to discard.           Default: 1
        from_hand   bool  Allow discarding from hand.  Default: True
        from_equip  bool  Allow discarding equipment.  Default: True

    TargetSpec:
        requires_target = True   — must have a target
        allow_self      = False
        must_be_alive   = True

    Cat Balou specifics:
      In the physical game the acting player *chooses* which card to discard
      (from hand OR equipment).  The TODO in _select_card() marks where player
      input will be wired in.  Currently picks randomly for determinism.

    Chaining:
        metadata["discarded_ids"] lists card definition ids that were discarded.
        A follow-up effect that triggers "if equipment was discarded" can read this.

    Usage in card JSON::

        # Cat Balou
        {"type": "force_discard", "params": {"count": 1,
                                              "from_hand": true,
                                              "from_equip": true}}
    """

    effect_type = EffectType.FORCE_DISCARD
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

        # Build the pool of discardable cards.
        from game.card import Card
        pool: list[Card] = []
        if from_hand:
            pool.extend(target.hand)
        if from_equip:
            pool.extend(target.equipment.values())

        if not pool:
            logger.debug(
                "ForceDiscardEffect: %s has no discardable cards.",
                target.player_id,
            )
            return EffectResult.skipped("target has no discardable cards")

        discarded_ids: list[str] = []
        discarded_from_equip: list[str] = []

        for _ in range(min(count, len(pool))):
            card = self._select_card(ctx, pool)
            if card is None:
                break

            # Remove from the right location.
            is_equipped = card.uid in target.equipment
            if is_equipped:
                target.unequip(card.uid)
                discarded_from_equip.append(card.id)
            else:
                target.lose_card_from_hand(card.uid)

            ctx.state.deck.discard(card)
            discarded_ids.append(card.id)
            pool.remove(card)

            ctx.game.bus.emit(CardDiscardedEvent(
                player_id=target.player_id,
                card_id=card.id,
                card_name=card.name,
            ))

        logger.debug(
            "%s forced %s to discard: %s.",
            ctx.acting_player.player_id, target.player_id, discarded_ids,
        )

        return EffectResult(
            applied=bool(discarded_ids),
            metadata={
                "discarded_ids": discarded_ids,
                "discarded_from_equip": discarded_from_equip,
                "discard_count": len(discarded_ids),
                "from_player": target.player_id,
            },
        )

    def _select_card(self, ctx: "EffectContext", pool: list) -> "Any | None":
        """
        Choose which card to force-discard.

        TODO: Replace with player-choice logic from action payload.
              The acting player should pick which card they want discarded.
        """
        if not pool:
            return None
        rng = random.Random(ctx.state.seed)
        return rng.choice(pool)


# ---------------------------------------------------------------------------
# DiscardCardEffect — alias under the DISCARD_CARD EffectType
# ---------------------------------------------------------------------------

class DiscardCardEffect(ForceDiscardEffect):
    """
    Same behaviour as ForceDiscardEffect, registered under DISCARD_CARD.

    WHY TWO TYPES:
      The card data distinguishes "discard" (remove without gain for actor)
      and "steal" (actor gains the card).  The target experience of both is
      losing a card.  Having separate EffectTypes lets JSON card definitions
      be explicit, and lets a future "anti-discard" reaction distinguish them.
    """

    effect_type = EffectType.DISCARD_CARD


# ---------------------------------------------------------------------------
# Passive equipment effects — resolve() is always a no-op
# ---------------------------------------------------------------------------

class RangePassiveEffect(Effect):
    """
    Passive: increases the effective range opponents must cover to target
    this player.  Applied via Player.defense_range_bonus (player.py) which
    reads on_equip effects at query time.

    Registered to suppress "no handler" warnings and allow introspection.

    Params (in on_equip of CardDefinition):
        range_bonus   int   Extra range added.   Default: 1

    Example card: Mustang (+1 defence range).
    """

    effect_type = EffectType.SET_RANGE_BONUS
    target_spec = TargetSpec()

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        # Passive — no active resolution.  Value is read by range_between().
        bonus = ctx.effect.params.get("range_bonus", 1)
        logger.debug(
            "RangePassiveEffect: passive +%d range registered for %s (card: %s).",
            bonus, ctx.acting_player.player_id, ctx.card.id,
        )
        return EffectResult.skipped("passive — applied via equipment stat")


class AttackRangePassiveEffect(Effect):
    """
    Passive: increases this player's attack range.
    Applied via Player.attack_range_bonus.

    Params (in on_equip of CardDefinition):
        range_bonus   int   Extra attack range.  Default: 1

    Example cards: Scope (+1 attack), Volcanic (weapon — handled as tag).
    """

    effect_type = EffectType.SET_ATTACK_RANGE
    target_spec = TargetSpec()

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        bonus = ctx.effect.params.get("range_bonus", 1)
        logger.debug(
            "AttackRangePassiveEffect: passive +%d attack range for %s.",
            bonus, ctx.acting_player.player_id,
        )
        return EffectResult.skipped("passive — applied via equipment stat")


# ---------------------------------------------------------------------------
# SkipDrawPhaseEffect — blue card that skips a turn's draw phase (e.g. Jail)
# ---------------------------------------------------------------------------

class SkipDrawPhaseEffect(Effect):
    """
    Blue card played on an opponent.  At the start of that opponent's turn,
    they flip a card: on a heart, they escape Jail and play normally; otherwise
    they skip their draw and play phases.

    CURRENT STATE:
      This is a stub registered to suppress warnings.  The full "flip and check"
      mechanic requires the GREEN card / delayed-effect system (see enums.py
      CardType.GREEN TODO).

    HOW THE FULL IMPLEMENTATION WOULD WORK:
      1. Jail is equipped on the target (BLUE card → target.equipment).
      2. At TurnPhase.DRAW for that player, the engine checks if they have
         Jail equipped.
      3. If yes: flip the top card of the deck.
             - Heart suit → unequip Jail, proceed normally.
             - Other suit → skip to TurnPhase.END (no draw, no play).
      4. Emit a SkipDrawPhaseEvent (new EventType needed).

    TODO: Implement when delayed/conditional turn-start effects are designed.

    Params:
        suit_escape   str   Suit that allows escaping. Default: "hearts"
    """

    effect_type = EffectType.SKIP_DRAW_PHASE
    target_spec = TargetSpec(requires_target=True, allow_self=False, must_be_alive=True)

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        # TODO: implement delayed turn-start flip mechanic.
        # For now: log and return skipped so blue card can still be equipped.
        logger.debug(
            "SkipDrawPhaseEffect: stub — Jail mechanic not yet implemented. "
            "Card will be equipped but have no turn-phase effect."
        )
        return EffectResult.skipped("skip_draw_phase mechanic not yet implemented")


# ---------------------------------------------------------------------------
# TODO: StatusCondition system (future expansion)
# ---------------------------------------------------------------------------
#
# When implemented, this module will also contain:
#
#   @dataclass
#   class StatusCondition:
#       name: str                        # "poisoned", "jailed", "on_fire"
#       turns_remaining: int | None      # None = permanent until removed
#       trigger_phase: TurnPhase         # when to apply
#       effect_type: EffectType          # what happens each trigger
#       params: dict[str, Any]
#
#   class AddStatusEffect(Effect):
#       effect_type = EffectType.ADD_STATUS   # new EffectType needed
#       async def resolve(ctx) -> EffectResult:
#           condition = StatusCondition(...)
#           ctx.target_player.status_conditions[condition.name] = condition
#           ...
#
#   class RemoveStatusEffect(Effect):
#       effect_type = EffectType.REMOVE_STATUS
#       ...
#
# Player.status_conditions: dict[str, StatusCondition] would be added to player.py.
# The Game class would check conditions at each TurnPhase transition.
