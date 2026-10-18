"""
server/cards/registry.py

CardRegistry — the runtime card database for one server process.

WHAT IT IS
──────────
  CardRegistry sits between the loader (disk → validated schemas) and the
  game engine (CardCatalogue → Card instances).  It is the single source of
  truth for:

    - Which card definitions are available (wraps CardCatalogue).
    - Which packs are loaded and in what order.
    - How to build a shuffled deck for a specific game mode.
    - Where card image assets live on disk.

WHAT IT IS NOT
──────────────
  - Not responsible for reading files (CardLoader owns that).
  - Not responsible for game rules (Game / Effect classes own that).
  - Not a global singleton — Room creates its own registry per session,
    or the server shares one pre-loaded registry for all rooms.

DECK BUILDING
─────────────
  build_deck(mode_id) assembles a list[Card] ready for Game.start().

  The base classic mode uses all cards from all loaded packs.
  Future game modes can request a subset:

      registry.build_deck("dodge_city")   # only that expansion's cards
      registry.build_deck("classic")      # base pack only
      registry.build_deck()               # all loaded packs

  This is controlled by a DeckRecipe — a lightweight config that says
  which packs to include and how many copies of each card to include
  (allowing game modes to override copy counts without re-loading JSON).

MODDING
───────
  Mod packs are loaded after base packs.  If a mod card has the same id
  as a base card, the registry replaces the definition (override semantics).
  The original is logged for debugging.

  This lets mods:
    - Buff / nerf existing cards (replace the definition).
    - Add entirely new cards (new id).
    - Remove cards (future: a "disabled" flag in CardSchema).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from game.card import Card, CardCatalogue, CardDefinition
from game.enums import CardSuit, CardRank

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PackMeta — lightweight record of a loaded pack
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackMeta:
    """
    Immutable metadata about a loaded card pack.

    Stored in CardRegistry._packs so the server can report which packs
    are active (lobby UI, mod listing, debug endpoints).
    """
    pack_id: str
    pack_name: str
    version: str
    description: str
    author: str
    card_count: int
    source_file: str    # absolute path to the JSON file


# ---------------------------------------------------------------------------
# DeckRecipe — declarative deck construction config
# ---------------------------------------------------------------------------

@dataclass
class DeckRecipe:
    """
    Declarative specification for building a deck from loaded cards.

    WHY A RECIPE OBJECT:
      Different game modes need different deck compositions without requiring
      separate JSON files.  A recipe is cheap to construct in code and easy
      to serialise for game-mode configs.

    Fields:
        included_packs  If set, only cards from these pack_ids are included.
                        None means all loaded packs.
        excluded_ids    Card ids to exclude regardless of pack.
                        Useful for game modes that don't use certain cards.
        copy_overrides  Override the copy count for specific card ids.
                        { "bang": 4 } forces exactly 4 Bang! in the deck
                        regardless of how many copies are in the JSON.
        mode_id         Informational — matches the game mode string in GameState.
    """
    included_packs: list[str] | None = None
    excluded_ids: set[str] = field(default_factory=set)
    copy_overrides: dict[str, int] = field(default_factory=dict)
    mode_id: str = "classic"

    # Built-in recipes
    @classmethod
    def all_packs(cls) -> "DeckRecipe":
        """Include every card from every loaded pack."""
        return cls(mode_id="classic")

    @classmethod
    def packs_only(cls, *pack_ids: str) -> "DeckRecipe":
        """Include only cards from the named packs."""
        return cls(included_packs=list(pack_ids))

    @classmethod
    def exclude(cls, *card_ids: str) -> "DeckRecipe":
        """Use all packs but skip specific card ids."""
        return cls(excluded_ids=set(card_ids))


# ---------------------------------------------------------------------------
# CardRegistry
# ---------------------------------------------------------------------------

class CardRegistry:
    """
    Runtime card database for one server process.

    Wraps CardCatalogue (engine's definition store) and adds:
      - Pack tracking (load order, version, source file).
      - Deck construction via DeckRecipe.
      - Asset path resolution.
      - Override semantics for mod packs.

    Usage::

        registry = CardRegistry(assets_root=Path("assets/cards"))
        # Populated by CardLoader:
        registry.register_pack(pack_meta, definitions, copies_map)

        # Build a deck for game start:
        cards = registry.build_deck()
        await room.start_game(cards)

        # Look up a definition:
        defn = registry.get_definition("bang")

        # Resolve an image path:
        path = registry.asset_path("bang")   # Path("assets/cards/bang.webp")
    """

    def __init__(
        self,
        assets_root: Path | None = None,
        asset_extension: str = "webp",
    ) -> None:
        self._catalogue: CardCatalogue = CardCatalogue()

        # Ordered list of loaded packs (load order matters for overrides).
        self._packs: list[PackMeta] = []

        # Map card_id → list of (suit, rank) tuples for deck building.
        # Each tuple becomes one Card instance.
        self._copies: dict[str, list[tuple[CardSuit, CardRank]]] = {}

        # Asset root for image resolution.
        self._assets_root: Path = assets_root or Path("assets") / "cards"
        self._asset_extension: str = asset_extension.lstrip(".")

        # Per-card asset name override (from CardSchema.asset_name).
        self._asset_names: dict[str, str] = {}   # card_id → filename stem

        # Track which pack each card_id came from (for mod override logging).
        self._card_pack_source: dict[str, str] = {}   # card_id → pack_id

    # ------------------------------------------------------------------
    # Registration  (called by CardLoader)
    # ------------------------------------------------------------------

    def register_pack(
        self,
        meta: PackMeta,
        definitions: list[CardDefinition],
        copies_map: dict[str, list[tuple[CardSuit, CardRank]]],
        asset_names: dict[str, str] | None = None,
    ) -> None:
        """
        Register a fully-validated pack into the registry.

        Called by CardLoader after a pack JSON file is loaded and converted.
        Not intended to be called directly by application code.

        Args:
            meta:         Pack metadata (id, name, version, etc.).
            definitions:  CardDefinition objects for every card in the pack.
            copies_map:   { card_id: [(suit, rank), ...] } for deck building.
            asset_names:  { card_id: "filename_stem" } overrides.
        """
        for defn in definitions:
            existing_pack = self._card_pack_source.get(defn.id)
            if existing_pack is not None:
                # Override: log and replace in catalogue.
                logger.info(
                    "Pack %r overrides card %r (was from %r).",
                    meta.pack_id, defn.id, existing_pack,
                )
                # CardCatalogue doesn't support replace, so we reach inside.
                self._catalogue._definitions[defn.id] = defn
            else:
                self._catalogue.register(defn)

            self._card_pack_source[defn.id] = meta.pack_id

        # Merge copy lists (override replaces entirely for same card_id).
        self._copies.update(copies_map)

        # Merge asset name overrides.
        if asset_names:
            self._asset_names.update(asset_names)

        self._packs.append(meta)
        logger.info(
            "Registered pack %r (%d cards).", meta.pack_id, len(definitions)
        )

    # ------------------------------------------------------------------
    # Deck building
    # ------------------------------------------------------------------

    def build_deck(self, recipe: DeckRecipe | None = None) -> list[Card]:
        """
        Build a shuffled-ready list of Card instances from loaded definitions.

        Each (card_id, suit, rank) tuple in _copies becomes one Card.
        The list is returned in definition order — Deck.shuffle() will
        randomise it using the seeded RNG.

        Args:
            recipe:  Optional DeckRecipe controlling which cards to include.
                     Defaults to DeckRecipe.all_packs() (every loaded card).

        Returns:
            list[Card] ready to pass to Deck.load() via room.start_game().

        Raises:
            ValueError  if the recipe references unknown pack ids.
            RuntimeError if no cards match the recipe (empty deck).
        """
        if recipe is None:
            recipe = DeckRecipe.all_packs()

        # Validate requested packs exist.
        if recipe.included_packs is not None:
            loaded_ids = {m.pack_id for m in self._packs}
            unknown = set(recipe.included_packs) - loaded_ids
            if unknown:
                raise ValueError(
                    f"DeckRecipe references unknown pack(s): {sorted(unknown)}. "
                    f"Loaded packs: {sorted(loaded_ids)}"
                )

        cards: list[Card] = []

        for card_id, copy_list in self._copies.items():
            # Pack filter
            if recipe.included_packs is not None:
                source = self._card_pack_source.get(card_id, "")
                if source not in recipe.included_packs:
                    continue

            # Exclusion filter
            if card_id in recipe.excluded_ids:
                continue

            try:
                defn = self._catalogue.get(card_id)
            except KeyError:
                logger.warning("build_deck: no definition for card_id %r — skipped.", card_id)
                continue

            # Copy count: use override if set, else use JSON-defined copies.
            if card_id in recipe.copy_overrides:
                target_count = recipe.copy_overrides[card_id]
                # Cycle through the suit/rank list to fill the target count.
                actual_copies = [
                    copy_list[i % len(copy_list)]
                    for i in range(target_count)
                ]
            else:
                actual_copies = copy_list

            for suit, rank in actual_copies:
                cards.append(Card.from_definition(defn, suit=suit, rank=rank))

        if not cards:
            raise RuntimeError(
                "build_deck produced an empty deck. "
                "Check that packs are loaded and the recipe matches at least one card."
            )

        logger.info(
            "Built deck: %d cards from %d definition(s).",
            len(cards), len({c.id for c in cards}),
        )
        return cards

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_definition(self, card_id: str) -> CardDefinition:
        """Retrieve a CardDefinition by id. Raises KeyError if not found."""
        return self._catalogue.get(card_id)

    def all_definitions(self) -> list[CardDefinition]:
        """All registered CardDefinitions, in registration order."""
        return self._catalogue.all()

    def loaded_packs(self) -> list[PackMeta]:
        """Metadata for every loaded pack, in load order."""
        return list(self._packs)

    def has_card(self, card_id: str) -> bool:
        try:
            self._catalogue.get(card_id)
            return True
        except KeyError:
            return False

    # ------------------------------------------------------------------
    # Asset path resolution
    # ------------------------------------------------------------------

    def asset_path(self, card_id: str) -> Path:
        """
        Resolve the filesystem path to a card's image asset.

        Uses the asset_name override from CardSchema if set, otherwise
        defaults to the card_id as the filename stem.

        The returned path is not guaranteed to exist — callers should
        check existence if they need to serve the file.

        Example::

            registry.asset_path("bang")
            # → Path("assets/cards/bang.webp")

            # With override: CardSchema.asset_name = "bang_card"
            # → Path("assets/cards/bang_card.webp")
        """
        stem = self._asset_names.get(card_id, card_id)
        return self._assets_root / f"{stem}.{self._asset_extension}"

    def asset_url(self, card_id: str, base_url: str = "/assets/cards") -> str:
        """
        Return the URL path for a card's image asset.
        Useful for building frontend card data payloads.

        Example::

            registry.asset_url("bang")
            # → "/assets/cards/bang.webp"
        """
        stem = self._asset_names.get(card_id, card_id)
        return f"{base_url.rstrip('/')}/{stem}.{self._asset_extension}"

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a dict summarising loaded state. Useful for debug endpoints."""
        return {
            "packs_loaded": len(self._packs),
            "total_definitions": len(self._catalogue),
            "total_deck_copies": sum(len(v) for v in self._copies.values()),
            "packs": [
                {
                    "pack_id": m.pack_id,
                    "pack_name": m.pack_name,
                    "version": m.version,
                    "card_count": m.card_count,
                }
                for m in self._packs
            ],
        }

    def __len__(self) -> int:
        return len(self._catalogue)

    def __repr__(self) -> str:
        pack_ids = [m.pack_id for m in self._packs]
        return f"CardRegistry(packs={pack_ids}, cards={len(self)})"
