"""
server/effects/damage.py

Damage-related effects: single-target, area-of-effect, and negation.

REACTION SEAM:
  DamageEffect has an explicit async hook (_request_reaction) that is called
  before damage is applied.  Right now it returns False (no reaction possible).
  When the interrupt system is built, this method becomes a real async wait:

      future = asyncio.Future()
      bus.emit(ReactionRequestedEvent(...))
      # networking layer resolves the future when player responds (or times out)
      negated = await asyncio.wait_for(future, timeout=10.0)

  Nothing else in this file changes when that happens.

CHAINING EXAMPLE — "Dynamite" card (Bang! expansion):
  At the start of a turn, flip the top card.  If it's a spade 2-9, deal 3
  damage to the holder (bypassing Missed!).  This would be modelled as a
  DamageEffect with params={"amount": 3, "bypass_reaction": True}.
  The bypass flag is checked in _request_reaction().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from effects.base import Effect, EffectResult, TargetSpec
from game.enums import EffectType
from game.events import DamageDealtEvent, ReactionRequestedEvent

if TYPE_CHECKING:
    from game.game import EffectContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DamageEffect — single-target damage (e.g. Bang!)
# ---------------------------------------------------------------------------

class DamageEffect(Effect):
    """
    Deal N damage to one target player.

    Params (from CardEffect.params):
        amount          int   Damage to deal.           Default: 1
        bypass_reaction bool  Skip Missed! opportunity. Default: False

    TargetSpec:
        requires_target = True   — must have a target
        must_be_alive   = True   — can't target eliminated players
        max_range driven by CardDefinition.max_range, not the effect itself.
        (Range is validated by Game._validate_brown_play before resolve().)

    Reaction hook:
        Before applying damage, _request_reaction() is called.
        If it returns True the damage is negated and EffectResult.negated()
        is returned — no HP change, no DamageDealtEvent.

    Chaining:
        EffectResult.metadata["damage_dealt"] carries the actual amount for
        subsequent effects to read (e.g. a "lifesteal" follow-up effect).

    Usage in card JSON::

        {
          "id": "bang",
          "effects": [{"type": "deal_damage", "params": {"amount": 1}}]
        }
    """

    effect_type = EffectType.DEAL_DAMAGE
    target_spec = TargetSpec(requires_target=True, must_be_alive=True)

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        err = self.validate_target(ctx)
        if err:
            logger.warning("DamageEffect skipped: %s", err)
            return EffectResult.skipped(err)

        target = ctx.target_player          # guaranteed non-None by validate_target
        amount: int = ctx.effect.params.get("amount", 1)
        bypass: bool = ctx.effect.params.get("bypass_reaction", False)

        # --- reaction window ---
        if not bypass:
            negated = await self._request_reaction(ctx, target)
            if negated:
                logger.debug(
                    "DamageEffect negated by reaction: %s → %s",
                    ctx.acting_player.player_id, target.player_id,
                )
                return EffectResult.negated()

        # --- apply damage ---
        new_hp = target.take_damage(amount)

        ctx.game.bus.emit(DamageDealtEvent(
            source_id=ctx.acting_player.player_id,
            target_id=target.player_id,
            amount=amount,
            cause=ctx.card.name,
        ))

        logger.debug(
            "%s dealt %d damage to %s (hp now %d).",
            ctx.acting_player.player_id, amount, target.player_id, new_hp,
        )

        # --- elimination check ---
        eliminated = False
        if new_hp <= 0:
            ctx.game._eliminate_player(target, eliminated_by=ctx.acting_player.player_id)
            eliminated = True

        return EffectResult(
            applied=True,
            metadata={
                "damage_dealt": amount,
                "target_id": target.player_id,
                "target_eliminated": eliminated,
                "new_hp": new_hp,
            },
        )

    async def _request_reaction(
        self,
        ctx: "EffectContext",
        target: "Any",   # Player
    ) -> bool:
        """
        Ask the target player if they want to react (play Missed!, etc.).

        Returns True if the damage should be negated, False to proceed.

        CURRENT STATE: stub — always returns False (no reaction system yet).

        FUTURE STATE:
          1. Check if target has any NEGATE_DAMAGE cards in hand.
          2. If yes, emit ReactionRequestedEvent.
          3. Await an asyncio.Future that the networking layer resolves.
          4. Return whether the player played a valid reaction.

        The event is emitted here even in stub form so the networking layer
        can start building the timer UI without touching this file later.

        TODO: replace stub body with the real async await when the reaction
              system (networking/reactions.py) is implemented.
        """
        # Emit the event so subscribers know a reaction window opened.
        # The networking layer currently ignores it; it won't after TODO is done.
        has_reaction_card = any(
            any(e.type == EffectType.NEGATE_DAMAGE for e in c.effects)
            for c in target.hand
        )

        if has_reaction_card:
            ctx.game.bus.emit(ReactionRequestedEvent(
                trigger_card_id=ctx.card.uid,
                reacting_player_ids=[target.player_id],
                timeout_seconds=ctx.effect.params.get("reaction_timeout", 10.0),
            ))
            # TODO: await asyncio.Future resolved by networking layer
            # For now: no automatic reaction, human must play it explicitly
            logger.debug(
                "Reaction window opened for %s (stub — not awaiting).",
                target.player_id,
            )

        return False   # stub: damage never negated automatically


# ---------------------------------------------------------------------------
# DamageAllEffect — area damage (e.g. Gatling)
# ---------------------------------------------------------------------------

class DamageAllEffect(Effect):
    """
    Deal N damage to ALL other players simultaneously.

    Params:
        amount          int   Damage per player.        Default: 1
        bypass_reaction bool  Skip Missed! opportunity. Default: False

    WHY NOT REUSE DamageEffect:
      DamageAllEffect iterates alive players itself because the target list
      is dynamic (all alive others).  The EffectContext only carries one
      target_player, which is None for area effects.  Reusing DamageEffect
      would require injecting a list of targets into EffectContext — a bigger
      contract change than is warranted right now.

    TODO: When reactions are implemented, each player in the loop needs their
    own reaction window, ideally in parallel (all get the same 10s window).

    Chaining:
        metadata["eliminations"] lists player_ids that were eliminated.

    Usage in card JSON::

        {
          "id": "gatling",
          "effects": [{"type": "deal_damage_all", "params": {"amount": 1}}]
        }
    """

    effect_type = EffectType.DEAL_DAMAGE_ALL
    target_spec = TargetSpec()   # no single target required

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        amount: int = ctx.effect.params.get("amount", 1)
        bypass: bool = ctx.effect.params.get("bypass_reaction", False)

        others = [
            p for p in ctx.state.alive_players()
            if p.player_id != ctx.acting_player.player_id
        ]

        if not others:
            return EffectResult.skipped("no other alive players")

        eliminations: list[str] = []
        total_damage = 0

        for target in others:
            # Each player gets their own reaction window.
            # TODO: run these concurrently with asyncio.gather() when reactions live.
            negated = False
            if not bypass:
                negated = await self._check_reaction(ctx, target)

            if negated:
                logger.debug(
                    "DamageAllEffect negated for %s by reaction.", target.player_id
                )
                continue

            new_hp = target.take_damage(amount)
            total_damage += amount

            ctx.game.bus.emit(DamageDealtEvent(
                source_id=ctx.acting_player.player_id,
                target_id=target.player_id,
                amount=amount,
                cause=ctx.card.name,
            ))

            if new_hp <= 0:
                ctx.game._eliminate_player(target, eliminated_by=ctx.acting_player.player_id)
                eliminations.append(target.player_id)

        return EffectResult(
            applied=total_damage > 0,
            metadata={
                "damage_per_player": amount,
                "total_damage": total_damage,
                "targets_hit": len(others) - len(eliminations),
                "eliminations": eliminations,
            },
        )

    async def _check_reaction(self, ctx: "EffectContext", target: "Any") -> bool:
        """Stub reaction check — same pattern as DamageEffect._request_reaction."""
        has_reaction_card = any(
            any(e.type == EffectType.NEGATE_DAMAGE for e in c.effects)
            for c in target.hand
        )
        if has_reaction_card:
            ctx.game.bus.emit(ReactionRequestedEvent(
                trigger_card_id=ctx.card.uid,
                reacting_player_ids=[target.player_id],
                timeout_seconds=10.0,
            ))
            # TODO: await reaction future
        return False


# ---------------------------------------------------------------------------
# NegateDamageEffect — reaction card (e.g. Missed!)
# ---------------------------------------------------------------------------

class NegateDamageEffect(Effect):
    """
    Cancel incoming damage.  Played as a REACTION, not as a main action.

    HOW THIS FITS THE REACTION SYSTEM:
      When DamageEffect._request_reaction() emits ReactionRequestedEvent,
      the networking layer gives the target player a window to play a card
      with NEGATE_DAMAGE.  If they do, the networking layer resolves the
      pending Future with True, and DamageEffect returns EffectResult.negated().

      This effect class is registered so:
        a) The card definition can reference the EffectType.
        b) Tests can invoke it directly.
        c) The reaction system can confirm the played card has this effect.

    Currently a stub — the resolution happens in DamageEffect, not here.
    """

    effect_type = EffectType.NEGATE_DAMAGE
    target_spec = TargetSpec()   # no target — applies to self as reaction

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        # In a reaction context, this effect is resolved by DamageEffect
        # checking the Future that the networking layer resolved.
        # If somehow called directly (e.g. in tests), we log and return.
        logger.debug(
            "NegateDamageEffect.resolve() called directly — "
            "normally resolved via reaction system."
        )
        return EffectResult(
            applied=True,
            metadata={"negated_by": ctx.acting_player.player_id},
        )
