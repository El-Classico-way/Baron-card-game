"""
server/cards/loader.py

CardLoader — reads JSON card pack files and populates a CardRegistry.

RESPONSIBILITIES
────────────────
  1. Discover pack files on disk (single file, directory scan, or explicit list).
  2. Parse JSON and validate against PackSchema (Pydantic).
  3. Convert validated schemas → engine types (CardDefinition, CardEffect).
  4. Validate EffectType strings against the live EffectType enum.
  5. Register everything into a CardRegistry.
  6. Report clear, actionable errors for bad JSON.

WHAT IT DOES NOT DO
───────────────────
  - No game logic.
  - No networking.
  - No deck shuffling (that's Deck.shuffle()).

FILE DISCOVERY
──────────────
  load_file(path)       Load a single JSON file.
  load_directory(dir)   Load all *.json files in a directory, sorted by name.
  load_packs(paths)     Load an explicit list of files in order.

  Load order matters for mod overrides: later files win on id collisions.
  Convention: name files with a numeric prefix to control order:
      00_base.json
      10_dodge_city.json
      50_my_mod.json

ERROR HANDLING
──────────────
  Every public method raises CardLoadError (a clear subclass of ValueError)
  on any validation failure.  The server startup code wraps loader calls in
  try/except and logs the error before exiting or falling back to a safe state.

  Individual card errors inside a pack do not abort the whole pack — they are
  collected and reported together so a modder can fix all problems at once.

EFFECT TYPE VALIDATION
──────────────────────
  EffectType values in JSON are validated against the live EffectType enum.
  This means:
    - Typos in effect type strings are caught immediately on load.
    - New effect types only require adding to EffectType enum + an Effect
      class in effects/ — no loader changes needed.
    - Unknown effect types produce a CardLoadError with the card id, field
      name, and the unknown value for easy debugging.

EXAMPLE JSON FILE
─────────────────
  See cards/data/base.json for the canonical example.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from game.card import Card, CardDefinition, CardEffect
from game.enums import CardSuit, CardRank, CardType, EffectType

from cards.models import CardSchema, CopySchema, EffectSchema, PackSchema
from cards.registry import CardRegistry, DeckRecipe, PackMeta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class CardLoadError(ValueError):
    """
    Raised when a card pack JSON file fails validation or conversion.

    Carries the source file path and a list of individual error messages
    so callers can surface all problems at once.

    Usage::

        try:
            loader.load_file("base.json")
        except CardLoadError as e:
            for msg in e.errors:
                logger.error("Card load error: %s", msg)
    """

    def __init__(self, source: str, errors: list[str]) -> None:
        self.source = source
        self.errors = errors
        bullet_list = "\n  ".join(errors)
        super().__init__(
            f"Failed to load card pack {source!r}:\n  {bullet_list}"
        )


# ---------------------------------------------------------------------------
# CardLoader
# ---------------------------------------------------------------------------

class CardLoader:
    """
    Loads card pack JSON files and populates a CardRegistry.

    Usage::

        registry = CardRegistry()
        loader   = CardLoader(registry)

        loader.load_file("cards/data/base.json")
        loader.load_directory("cards/data/expansions/")

        cards = registry.build_deck()
        await room.start_game(cards)

    For simple cases, use the module-level convenience function::

        registry = load_default_registry()
        cards = registry.build_deck()
    """

    def __init__(self, registry: CardRegistry) -> None:
        self._registry = registry

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def load_file(self, path: str | Path) -> PackMeta:
        """
        Load a single card pack JSON file into the registry.

        Returns the PackMeta for the loaded pack.
        Raises CardLoadError if the file is missing, invalid JSON, or
        fails schema validation.
        """
        path = Path(path)
        source = str(path)

        if not path.exists():
            raise CardLoadError(source, [f"File not found: {path}"])
        if not path.is_file():
            raise CardLoadError(source, [f"Path is not a file: {path}"])

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CardLoadError(source, [f"Cannot read file: {exc}"])

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CardLoadError(source, [f"Invalid JSON at line {exc.lineno}: {exc.msg}"])

        return self._load_pack_data(data, source)

    def load_directory(
        self,
        directory: str | Path,
        *,
        glob: str = "*.json",
        recursive: bool = False,
    ) -> list[PackMeta]:
        """
        Load all JSON files in a directory, sorted by filename.

        Files are loaded in alphabetical order so numeric prefixes control
        load priority (00_base.json before 10_expansion.json).

        Returns a list of PackMeta for every successfully loaded pack.
        Raises CardLoadError on the first file that fails.
        """
        directory = Path(directory)
        if not directory.is_dir():
            raise CardLoadError(str(directory), [f"Not a directory: {directory}"])

        pattern = f"**/{glob}" if recursive else glob
        files = sorted(directory.glob(pattern))

        if not files:
            logger.warning("load_directory: no JSON files found in %s", directory)
            return []

        loaded: list[PackMeta] = []
        for file in files:
            meta = self.load_file(file)
            loaded.append(meta)

        logger.info(
            "Loaded %d pack(s) from directory %s.", len(loaded), directory
        )
        return loaded

    def load_packs(self, paths: list[str | Path]) -> list[PackMeta]:
        """
        Load an explicit ordered list of pack files.

        Useful when the caller controls load order precisely (e.g. tests).
        """
        return [self.load_file(p) for p in paths]

    # ------------------------------------------------------------------
    # Internal: parse and register one pack
    # ------------------------------------------------------------------

    def _load_pack_data(self, data: dict[str, Any], source: str) -> PackMeta:
        """
        Validate raw dict data against PackSchema and register into registry.
        """
        # --- schema validation ---
        try:
            pack = PackSchema.model_validate(data)
        except ValidationError as exc:
            errors = [f"{e['loc']}: {e['msg']}" for e in exc.errors()]
            raise CardLoadError(source, errors)

        # --- check for duplicate ids within this pack ---
        dupes = pack.duplicate_ids()
        if dupes:
            raise CardLoadError(
                source,
                [f"Duplicate card id(s) in pack {pack.pack_id!r}: {dupes}"]
            )

        # --- convert each card, collecting all errors before raising ---
        definitions: list[CardDefinition] = []
        copies_map: dict[str, list[tuple[CardSuit, CardRank]]] = {}
        asset_names: dict[str, str] = {}
        card_errors: list[str] = []

        for card_schema in pack.cards:
            try:
                defn, copies = self._schema_to_definition(card_schema)
                definitions.append(defn)
                copies_map[defn.id] = copies
                if card_schema.asset_name:
                    asset_names[defn.id] = card_schema.asset_name
            except CardLoadError as exc:
                card_errors.extend(exc.errors)

        if card_errors:
            raise CardLoadError(source, card_errors)

        # --- register into registry ---
        meta = PackMeta(
            pack_id=pack.pack_id,
            pack_name=pack.pack_name,
            version=pack.version,
            description=pack.description,
            author=pack.author,
            card_count=len(definitions),
            source_file=source,
        )
        self._registry.register_pack(meta, definitions, copies_map, asset_names)
        logger.info(
            "Loaded pack %r v%s from %s (%d cards).",
            pack.pack_id, pack.version, source, len(definitions),
        )
        return meta

    # ------------------------------------------------------------------
    # Internal: convert CardSchema → engine types
    # ------------------------------------------------------------------

    def _schema_to_definition(
        self, schema: CardSchema
    ) -> tuple[CardDefinition, list[tuple[CardSuit, CardRank]]]:
        """
        Convert a validated CardSchema into a (CardDefinition, copies) pair.

        Returns:
            CardDefinition — the immutable engine template.
            copies         — list of (CardSuit, CardRank) for deck building.

        Raises CardLoadError if any EffectType string is unknown.
        """
        errors: list[str] = []

        # --- card type ---
        try:
            card_type = CardType(schema.card_type)
        except ValueError:
            errors.append(
                f"Card {schema.id!r}: unknown card_type {schema.card_type!r}"
            )
            card_type = CardType.BROWN   # placeholder to continue collecting errors

        # --- effects ---
        effects = self._convert_effects(schema.id, "effects", schema.effects, errors)
        on_equip = self._convert_effects(schema.id, "on_equip", schema.on_equip, errors)
        on_unequip = self._convert_effects(schema.id, "on_unequip", schema.on_unequip, errors)

        # --- copies ---
        copies = self._convert_copies(schema.id, schema.copies, errors)

        if errors:
            raise CardLoadError(f"card:{schema.id}", errors)

        definition = CardDefinition(
            id=schema.id,
            name=schema.name,
            card_type=card_type,
            effects=tuple(effects),
            on_equip=tuple(on_equip),
            on_unequip=tuple(on_unequip),
            tags=frozenset(schema.tags),
            max_range=schema.max_range,
            min_range=schema.min_range,
            description=schema.description,
        )
        return definition, copies

    def _convert_effects(
        self,
        card_id: str,
        field_name: str,
        effect_schemas: list[EffectSchema],
        errors: list[str],
    ) -> list[CardEffect]:
        """
        Convert a list of EffectSchema into CardEffect objects.

        Appends to `errors` rather than raising, so all bad effects in
        a card are reported together.
        """
        result: list[CardEffect] = []
        for i, es in enumerate(effect_schemas):
            try:
                effect_type = EffectType(es.type)
            except ValueError:
                known = [e.value for e in EffectType]
                errors.append(
                    f"Card {card_id!r} {field_name}[{i}]: "
                    f"unknown effect type {es.type!r}. "
                    f"Known types: {known}"
                )
                continue
            result.append(CardEffect(type=effect_type, params=dict(es.params)))
        return result

    def _convert_copies(
        self,
        card_id: str,
        copy_schemas: list[CopySchema],
        errors: list[str],
    ) -> list[tuple[CardSuit, CardRank]]:
        """
        Convert a list of CopySchema into (CardSuit, CardRank) tuples.

        CopySchema validators already normalise suit (lowercase) and rank
        (uppercase), so direct enum lookup should always succeed here.
        If somehow it doesn't, the error is appended rather than raised.
        """
        result: list[tuple[CardSuit, CardRank]] = []
        for i, cs in enumerate(copy_schemas):
            try:
                suit = CardSuit(cs.suit)
                rank = CardRank(cs.rank)
                result.append((suit, rank))
            except ValueError as exc:
                errors.append(
                    f"Card {card_id!r} copies[{i}]: {exc}"
                )
        return result


# ---------------------------------------------------------------------------
# Convenience: load the default data directory
# ---------------------------------------------------------------------------

def load_default_registry(
    data_dir: str | Path | None = None,
    assets_root: Path | None = None,
) -> CardRegistry:
    """
    Load all packs from the default data directory and return a ready registry.

    This is the normal server startup call:

        registry = load_default_registry()
        cards = registry.build_deck()
        await room.start_game(cards)

    Args:
        data_dir:     Directory containing *.json pack files.
                      Defaults to cards/data/ relative to this file.
        assets_root:  Root directory for card images.
                      Defaults to assets/cards/ relative to project root.

    Raises:
        CardLoadError  if any pack file fails validation.
        RuntimeError   if no packs are found / deck would be empty.
    """
    if data_dir is None:
        data_dir = Path(__file__).parent / "data"

    registry = CardRegistry(assets_root=assets_root)
    loader = CardLoader(registry)
    loader.load_directory(data_dir)

    if len(registry) == 0:
        raise RuntimeError(
            f"No cards loaded from {data_dir}. "
            "Ensure at least one *.json pack file exists in that directory."
        )

    logger.info("Default registry ready: %s", registry)
    return registry
