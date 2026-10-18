"""
server/effects/heal.py

Healing effects.

HEAL VARIANTS IN BANG!:
  Beer          — heal 1 HP, self only, 2+ players must be alive to use.
  Whisky        — heal 2 HP, self only (DoC expansion).
  Doc Elmore    — heal any player N HP (future expansion card).
  Tequila       — heal self by discarding another card (future).

All are captured by HealEffect with different params and TargetSpec.  No
new class is needed for each card — only different JSON definitions.

CHAINING EXAMPLE — "Whisky + Draw":
  A hypothetical card that heals 2 then draws 1 card would be defined as:

      effects: [
        {"type": "heal",       "params": {"amount": 2}},
        {"type": "draw_cards", "params": {"count": 1}}
      ]

  The two effects resolve in order; the heal result's metadata is available
  but the draw doesn't need it here.  If the draw should only fire "if heal
  actually added HP" (e.g. you were already at max), read
  ctx.prior_results[-1].metadata["actually_healed"] — once prior_results
  is added to EffectContext (TODO in base.py).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from effects.base import Effect, EffectResult, TargetSpec
from game.enums import EffectType
from game.events import PlayerHealedEvent

if TYPE_CHECKING:
    from game.game import EffectContext

logger = logging.getLogger(__name__)


class HealEffect(Effect):
    """
    Restore N HP to a target player (default: the acting player).

    Params (from CardEffect.params):
        amount        int   HP to restore.             Default: 1
        min_players   int   Minimum alive players for
                            effect to fire (Beer rule). Default: 1
                            Set to 2 to enforce the
                            "Beer has no effect in
                            last-two-players" rule.

    TargetSpec:
        requires_target = False  — heals self by default
        allow_self      = True   — self-targeting is the normal case
        must_be_alive   = True   — can't heal a dead player

    Target resolution:
        If ctx.target_player is set (Doc Elmore targeting another player),
        that player is healed.  Otherwise ctx.acting_player is healed.
        This keeps the TargetSpec flexible without needing two subclasses.

    Chaining:
        metadata["actually_healed"] carries the real amount (may be less
        than `amount` if target was near max HP).  A follow-up effect that
        draws cards "per HP healed" can read this.

    Usage in card JSON::

        # Beer
        {"type": "heal", "params": {"amount": 1, "min_players": 2}}

        # Whisky (DoC expansion)
        {"type": "heal", "params": {"amount": 2, "min_players": 2}}

        # Doc Elmore (future) — can target others
        {"type": "heal", "params": {"amount": 1}}
    """

    effect_type = EffectType.HEAL
    target_spec = TargetSpec(
        requires_target=False,
        allow_self=True,
        must_be_alive=True,
    )

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        amount: int = ctx.effect.params.get("amount", 1)
        min_alive: int = ctx.effect.params.get("min_players", 1)

        # --- minimum alive players check (Beer rule) ---
        alive_count = len(ctx.state.alive_players())
        if alive_count < min_alive:
            logger.debug(
                "HealEffect skipped: only %d alive player(s), need %d.",
                alive_count, min_alive,
            )
            return EffectResult.skipped(
                f"Needs at least {min_alive} alive players; only {alive_count} remain."
            )

        # --- resolve target: explicit target or self ---
        if ctx.target_player is not None:
            err = self.validate_target(ctx)
            if err:
                logger.warning("HealEffect target invalid: %s", err)
                return EffectResult.skipped(err)
            target = ctx.target_player
        else:
            target = ctx.acting_player

        # --- apply heal ---
        before_hp = target.hp
        actually_healed = target.heal(amount)

        ctx.game.bus.emit(PlayerHealedEvent(
            player_id=target.player_id,
            amount=actually_healed,
            new_hp=target.hp,
        ))

        logger.debug(
            "%s healed %s for %d HP (%d → %d / %d).",
            ctx.acting_player.player_id,
            target.player_id,
            actually_healed,
            before_hp,
            target.hp,
            target.max_hp,
        )

        return EffectResult(
            applied=actually_healed > 0,
            metadata={
                "actually_healed": actually_healed,
                "target_id": target.player_id,
                "new_hp": target.hp,
                "was_capped": actually_healed < amount,
            },
        )
