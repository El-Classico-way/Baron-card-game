"""
server/effects/base.py

Core abstractions for the composable effect system.

DESIGN OVERVIEW
───────────────
The effect system has three jobs:

  1. DESCRIBE  — CardEffect (in game/card.py) is the data descriptor that
                 lives in JSON and on CardDefinitions.  It says *what* should
                 happen and carries params.  No behaviour, just data.

  2. RESOLVE   — Effect subclasses (this package) are the behaviour objects.
                 Each one knows how to execute one verb: DamageEffect,
                 HealEffect, DrawEffect, …  They are looked up at runtime
                 via the EffectRegistry, so new verbs never require changes
                 to existing code.

  3. BRIDGE    — Game._resolve_effects() already iterates card.effects and
                 calls handler(ctx).  We make every Effect.resolve() match
                 that exact signature, so the dispatch table in game.py
                 becomes a one-liner:
                     handler = registry.get(effect.type)

WHY NOT A CLASS HIERARCHY PER CARD:
  See ARCHITECTURE.md — composition over inheritance.  Every card is data;
  every behaviour is a small, single-responsibility Effect object.

WHY ASYNC resolve():
  The reaction / interrupt system (Missed!, Jail check, Lucky Duke) will need
  to pause mid-resolution and wait for player input over a websocket.  Making
  resolve() async now costs nothing and avoids a painful refactor later.

  Synchronous effects (damage, heal, draw) just use plain awaitable bodies —
  `await asyncio.sleep(0)` is never needed; they just return immediately.
  The caller (game.py) uses asyncio.run() or is already in an async context.

CHAINING:
  Effects are chained by CardDefinition.effects — a tuple of CardEffect
  descriptors resolved in order.  Each Effect is independent and operates on
  the shared EffectContext, so side-effects from one (e.g. a player dying)
  are visible to the next.  If you need conditional chaining ("draw a card
  only if damage was dealt"), use EffectResult.metadata to pass signals
  between effects in the same chain.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Avoid circular imports — game.py imports from this package.
    # EffectContext is imported at runtime only where needed.
    from game.game import EffectContext

from game.enums import EffectType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EffectResult — what resolve() returns
# ---------------------------------------------------------------------------

@dataclass
class EffectResult:
    """
    The outcome of a single effect resolution.

    WHY A RESULT OBJECT:
      1. Callers can inspect what happened without subscribing to events.
      2. Chained effects can read prior results via EffectContext.
      3. The reaction system can check `was_negated` to skip damage application.

    Fields:
        applied     True if the effect changed game state.
        was_negated True if a reaction (Missed! etc.) cancelled the effect.
        metadata    Free-form dict for passing signals between chained effects.
                    e.g. {"damage_dealt": 2, "target_eliminated": True}
    """
    applied: bool = True
    was_negated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def negated(cls) -> "EffectResult":
        """Convenience: effect was cancelled by a reaction."""
        return cls(applied=False, was_negated=True)

    @classmethod
    def skipped(cls, reason: str = "") -> "EffectResult":
        """Convenience: effect had no valid target or precondition failed."""
        return cls(applied=False, was_negated=False, metadata={"reason": reason})


# ---------------------------------------------------------------------------
# TargetSpec — describes what a target must satisfy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TargetSpec:
    """
    Declarative description of targeting requirements for an effect.

    Used by TargetValidator.validate() — effects declare their needs, the
    validator checks them against the live GameState.  This keeps target
    logic out of individual effect classes.

    Fields:
        requires_target     Effect cannot fire without a target player.
        allow_self          Actor may target themselves (e.g. Beer).
        must_be_alive       Target must be alive (almost always True).
        max_range           If set, target must be within this effective range.
        require_hand_cards  Target must have at least N cards in hand.
        require_equipment   Target must have equipment (for steal/discard effects).
    """
    requires_target: bool = False
    allow_self: bool = False
    must_be_alive: bool = True
    max_range: int | None = None
    require_hand_cards: int = 0          # minimum hand size on target
    require_equipment: bool = False      # target must have at least one equipped card


# ---------------------------------------------------------------------------
# TargetValidator — stateless, reusable target checking
# ---------------------------------------------------------------------------

class TargetValidator:
    """
    Validates that a target player satisfies a TargetSpec.

    WHY SEPARATE FROM Effect:
      Target validation is identical across many effects (DamageEffect,
      StealEffect, ForceDiscardEffect all share the same "must be alive,
      in range" rules).  Centralising it avoids copy-pasting those guards
      into every effect class.

    All methods are static — no state, no dependencies — safe to call from
    anywhere including tests.

    Usage::

        spec = TargetSpec(requires_target=True, must_be_alive=True, max_range=1)
        err  = TargetValidator.validate(spec, actor, target, state)
        if err:
            return EffectResult.skipped(err)
    """

    @staticmethod
    def validate(
        spec: TargetSpec,
        actor: "Any",          # game.player.Player — avoid import cycle
        target: "Any | None",  # game.player.Player | None
        state: "Any",          # game.state.GameState
    ) -> str | None:
        """
        Validate target against spec.

        Returns None if valid, or an error string describing the problem.
        The caller decides how to handle the error (skip, fail, log).
        """
        if spec.requires_target and target is None:
            return "This effect requires a target."

        if target is None:
            return None   # optional target, absence is fine

        if not spec.allow_self and target.player_id == actor.player_id:
            return "You cannot target yourself with this effect."

        if spec.must_be_alive and not target.is_alive:
            return f"{target.display_name} is already eliminated."

        if spec.max_range is not None:
            effective = state.range_between(actor.player_id, target.player_id)
            if effective > spec.max_range:
                return (
                    f"{target.display_name} is out of range "
                    f"(effective {effective}, max {spec.max_range})."
                )

        if spec.require_hand_cards > 0 and target.hand_count() < spec.require_hand_cards:
            return f"{target.display_name} does not have enough cards in hand."

        if spec.require_equipment and not target.equipment:
            return f"{target.display_name} has no equipment to target."

        return None


# ---------------------------------------------------------------------------
# Effect — abstract base
# ---------------------------------------------------------------------------

class Effect(ABC):
    """
    Abstract base for all composable card effects.

    WHAT AN EFFECT IS:
      A single, focused behaviour unit.  It reads from EffectContext, mutates
      GameState via Player/Deck methods, and emits events via the EventBus.
      Nothing more.

    WHAT AN EFFECT IS NOT:
      - Not a card class.  Cards are data (CardDefinition + CardEffect tuples).
      - Not a handler function.  It's an object so it can carry its own
        TargetSpec and be introspected / tested independently.
      - Not aware of networking, rendering, or anything outside game state.

    SUBCLASSING RULES:
      1. Override `effect_type` to declare which EffectType this handles.
      2. Override `target_spec` to declare targeting requirements.
      3. Override `resolve()` with the actual behaviour.
      4. Call `super().__init__()` — base __init__ does nothing now but
         leaves room for future cross-cutting concerns (metrics, logging).

    ASYNC resolve():
      All resolve() methods are async.  Sync effects just return immediately
      without any await.  The reaction system will await them properly.

    Usage::

        class MyEffect(Effect):
            effect_type = EffectType.DEAL_DAMAGE
            target_spec = TargetSpec(requires_target=True, max_range=1)

            async def resolve(self, ctx: EffectContext) -> EffectResult:
                target = ctx.target_player
                amount = ctx.effect.params.get("amount", 1)
                target.take_damage(amount)
                ctx.state.bus.emit(DamageDealtEvent(...))
                return EffectResult(applied=True, metadata={"damage": amount})
    """

    # --- subclasses must set these ---

    effect_type: EffectType          # which EffectType enum value this handles
    target_spec: TargetSpec = TargetSpec()   # default: no target required

    def __init__(self) -> None:
        pass   # reserved for future cross-cutting init

    @abstractmethod
    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        """
        Apply this effect to the game state described by ctx.

        Contract:
          - Read acting_player, target_player, effect.params from ctx.
          - Mutate state only via Player / Deck methods.
          - Emit typed events via ctx.game.bus.emit().
          - Return an EffectResult describing what happened.
          - Never raise for expected game conditions; return skipped()/negated().
          - Do raise for programming errors (missing required fields, etc.).
        """
        ...

    def validate_target(self, ctx: "EffectContext") -> str | None:
        """
        Run standard target validation for this effect's TargetSpec.
        Returns None if valid, error string if not.
        Subclasses can override for non-standard logic.
        """
        return TargetValidator.validate(
            self.target_spec,
            ctx.acting_player,
            ctx.target_player,
            ctx.state,
        )

    async def __call__(self, ctx: "EffectContext") -> EffectResult:
        """
        Makes Effect instances callable as drop-in replacements for the
        handler functions in Game._effect_handlers.

        This is the bridge that lets game.py do:
            handler = registry.get(effect_type)
            result  = await handler(ctx)          # works whether handler
                                                  # is a function or Effect
        """
        return await self.resolve(ctx)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(type={self.effect_type})"


# ---------------------------------------------------------------------------
# NoopEffect — for passive / unimplemented effects
# ---------------------------------------------------------------------------

class NoopEffect(Effect):
    """
    Registered for effects that are passive (equipment bonuses applied
    elsewhere) or not yet implemented.  Prevents 'no handler' warnings.

    Usage::

        registry.register(NoopEffect(EffectType.SET_RANGE_BONUS))
    """

    def __init__(self, for_type: EffectType) -> None:
        super().__init__()
        self.effect_type = for_type

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        logger.debug("NoopEffect invoked for %s — no action taken.", self.effect_type)
        return EffectResult.skipped("noop")


# ---------------------------------------------------------------------------
# EffectRegistry — maps EffectType → Effect instance
# ---------------------------------------------------------------------------

class EffectRegistry:
    """
    Central registry mapping EffectType values to Effect instances.

    WHY A REGISTRY INSTEAD OF A DICT IN Game:
      1. Game doesn't need to know about every effect class — it just calls
         registry.resolve(ctx).
      2. Game modes can swap in a different registry with different behaviours
         for the same EffectType (e.g. High Noon damage rules).
      3. Tests can register mock effects without touching Game.
      4. New cards only need a new Effect class + registry.register() call.

    The registry holds one Effect instance per EffectType.  Effects are
    stateless (all context comes from EffectContext), so sharing instances
    is safe.

    Usage::

        registry = EffectRegistry()
        registry.register(DamageEffect())
        registry.register(HealEffect())
        registry.register(NoopEffect(EffectType.SET_RANGE_BONUS))

        # In game.py:
        result = await registry.resolve(ctx)
    """

    def __init__(self) -> None:
        self._effects: dict[EffectType, Effect] = {}

    def register(self, effect: Effect) -> None:
        """
        Register an Effect for its declared effect_type.
        Raises ValueError on duplicate (prevents silent overwrites).
        Call register_or_replace() if overwriting is intentional.
        """
        if effect.effect_type in self._effects:
            raise ValueError(
                f"EffectType {effect.effect_type!r} is already registered. "
                f"Use register_or_replace() to override deliberately."
            )
        self._effects[effect.effect_type] = effect
        logger.debug("Registered effect: %s", effect)

    def register_or_replace(self, effect: Effect) -> None:
        """Register, silently replacing any existing handler. For game modes."""
        self._effects[effect.effect_type] = effect

    def get(self, effect_type: EffectType) -> Effect | None:
        """Return the Effect for this type, or None if not registered."""
        return self._effects.get(effect_type)

    async def resolve(self, ctx: "EffectContext") -> EffectResult:
        """
        Look up and invoke the Effect for ctx.effect.type.

        Returns EffectResult.skipped() if no handler is registered, so
        unrecognised effects are logged but don't crash the engine.
        """
        effect_obj = self._effects.get(ctx.effect.type)
        if effect_obj is None:
            logger.warning(
                "No effect registered for %s on card %s.",
                ctx.effect.type, ctx.card.id,
            )
            return EffectResult.skipped(f"no handler for {ctx.effect.type}")

        return await effect_obj.resolve(ctx)

    async def resolve_all(
        self, ctx_list: "list[EffectContext]"
    ) -> list[EffectResult]:
        """
        Resolve a list of EffectContexts in order.
        Each result is independent; the list is returned for inspection.
        """
        results: list[EffectResult] = []
        for ctx in ctx_list:
            result = await self.resolve(ctx)
            results.append(result)
        return results

    def registered_types(self) -> list[EffectType]:
        return list(self._effects.keys())

    def __len__(self) -> int:
        return len(self._effects)

    def __repr__(self) -> str:
        types = [t.value for t in self._effects]
        return f"EffectRegistry({types})"


# ---------------------------------------------------------------------------
# build_default_registry() — constructs the standard effect set
# ---------------------------------------------------------------------------

def build_default_registry() -> EffectRegistry:
    """
    Build and return an EffectRegistry populated with all standard effects.

    Called once at game startup (or per-game if using mode-specific registries).
    Import is deferred to avoid circular imports between submodules.

    Usage::

        registry = build_default_registry()
        game = Game(state=state, bus=bus, registry=registry)
    """
    # Local imports to break the circular dependency chain.
    # effects/* import from game.*, not the other way around.
    from effects.damage import DamageEffect, DamageAllEffect, NegateDamageEffect
    from effects.heal import HealEffect
    from effects.draw import DrawCardsEffect, StealCardEffect, DrawFromPlayerEffect
    from effects.status import (
        ForceDiscardEffect, DiscardCardEffect,
        RangePassiveEffect, AttackRangePassiveEffect,
        SkipDrawPhaseEffect,
    )

    registry = EffectRegistry()

    # Combat
    registry.register(DamageEffect())
    registry.register(DamageAllEffect())
    registry.register(NegateDamageEffect())

    # Healing
    registry.register(HealEffect())

    # Card movement
    registry.register(DrawCardsEffect())
    registry.register(StealCardEffect())
    registry.register(DrawFromPlayerEffect())

    # Hand management
    registry.register(ForceDiscardEffect())
    registry.register(DiscardCardEffect())

    # Passive equipment (no active resolution — read by range_between())
    registry.register(RangePassiveEffect())
    registry.register(AttackRangePassiveEffect())
    registry.register(SkipDrawPhaseEffect())

    # Unimplemented — registered to suppress warnings
    registry.register(NoopEffect(EffectType.CANCEL_CARD))
    registry.register(NoopEffect(EffectType.EXTRA_CARD_PLAY))
    registry.register(NoopEffect(EffectType.CHANGE_TURN_ORDER))

    return registry
