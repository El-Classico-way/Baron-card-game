"""
server/effects/__init__.py

Public API surface of the effect system package.

External code (game modes, tests, future mod loaders) imports from here.
Internal submodules import from each other directly.

WHY EFFECTS IS A SEPARATE PACKAGE FROM game/:
  game/ is the pure engine — state, turns, events, players.
  effects/ is the behaviour layer — what cards *do*.
  Keeping them separate means:
    - Game modes can swap out the entire effect set (different registry).
    - Effects can be tested without instantiating a full Game.
    - New cards never require touching game/game.py.

TYPICAL USAGE::

    from effects import build_default_registry, EffectRegistry, EffectResult

    registry = build_default_registry()
    game = Game(state=state, bus=bus, registry=registry)
"""

from effects.base import (
    Effect,
    EffectResult,
    EffectRegistry,
    NoopEffect,
    TargetSpec,
    TargetValidator,
    build_default_registry,
)

from effects.damage import (
    DamageEffect,
    DamageAllEffect,
    NegateDamageEffect,
)

from effects.heal import (
    HealEffect,
)

from effects.draw import (
    DrawCardsEffect,
    DrawFromPlayerEffect,
    StealCardEffect,
)

from effects.status import (
    AttackRangePassiveEffect,
    DiscardCardEffect,
    ForceDiscardEffect,
    RangePassiveEffect,
    SkipDrawPhaseEffect,
)

__all__ = [
    # Core abstractions
    "Effect",
    "EffectResult",
    "EffectRegistry",
    "NoopEffect",
    "TargetSpec",
    "TargetValidator",
    "build_default_registry",
    # Damage
    "DamageEffect",
    "DamageAllEffect",
    "NegateDamageEffect",
    # Healing
    "HealEffect",
    # Card movement
    "DrawCardsEffect",
    "DrawFromPlayerEffect",
    "StealCardEffect",
    # Status / control
    "AttackRangePassiveEffect",
    "DiscardCardEffect",
    "ForceDiscardEffect",
    "RangePassiveEffect",
    "SkipDrawPhaseEffect",
]
