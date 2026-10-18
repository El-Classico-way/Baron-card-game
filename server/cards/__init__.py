"""
server/cards/__init__.py

Public API surface of the card loading system.

Typical server startup usage::

    from cards import load_default_registry

    registry = load_default_registry()
    cards = registry.build_deck()
    await room.start_game(cards)

For custom load orders or mod support::

    from cards import CardLoader, CardRegistry, DeckRecipe

    registry = CardRegistry()
    loader   = CardLoader(registry)
    loader.load_file("cards/data/base.json")
    loader.load_file("mods/my_expansion.json")

    recipe = DeckRecipe.packs_only("base")
    cards  = registry.build_deck(recipe)
"""

from cards.loader import CardLoadError, CardLoader, load_default_registry
from cards.models import CardSchema, CopySchema, EffectSchema, PackSchema
from cards.registry import CardRegistry, DeckRecipe, PackMeta

__all__ = [
    # Loader
    "CardLoadError",
    "CardLoader",
    "load_default_registry",
    # Models (validation schemas)
    "CardSchema",
    "CopySchema",
    "EffectSchema",
    "PackSchema",
    # Registry
    "CardRegistry",
    "DeckRecipe",
    "PackMeta",
]
