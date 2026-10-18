"""
server/cards/models.py

Pydantic validation models for card definition JSON files.

LAYER RESPONSIBILITIES
──────────────────────
  This file sits at the JSON → engine boundary.  Its only job is to:
    1. Accept untrusted JSON (from disk, mod packs, network).
    2. Validate it strictly and produce clear error messages.
    3. Convert it into the frozen engine types (CardDefinition, CardEffect).

  It knows about JSON structure.  It does NOT know about game rules.
  All game rules live in game/ and effects/.

SCHEMA OVERVIEW
───────────────
  A card pack JSON file has this top-level shape:

      {
        "pack_id":   "base",
        "pack_name": "Base Game",
        "version":   "1.0.0",
        "cards":     [ <CardSchema>, ... ]
      }

  Each card entry:

      {
        "id":          "bang",
        "name":        "Bang!",
        "card_type":   "brown",
        "description": "Deal 1 damage to a player within range 1.",
        "max_range":   1,
        "tags":        [],
        "effects": [
          { "type": "deal_damage", "params": { "amount": 1 } }
        ],
        "on_equip":   [],
        "on_unequip": [],
        "copies": [
          { "suit": "spades",   "rank": "A" },
          { "suit": "spades",   "rank": "K" }
        ]
      }

  The `copies` array drives deck construction — each entry becomes one
  physical Card instance with the given suit and rank.

LOCALISATION HOOK
─────────────────
  `name` and `description` are plain strings in v1.  When localisation is
  added, replace them with a locale key and a separate string table:

      "name_key": "card.bang.name",
      "desc_key": "card.bang.description"

  The loader then looks up the locale key in the active string table.
  The `CardSchema.name` field keeps its current meaning as the fallback/
  default locale value — no schema version bump needed.

MODDING
───────
  A mod pack is just another JSON file with a unique `pack_id`.
  The CardLoader (loader.py) loads packs in priority order — later packs
  can override cards from earlier packs by reusing the same `id`.
  `CardSchema.source_pack` is set by the loader, not the JSON, so modders
  can't falsely claim their card is from the base game.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Effect schema  (mirrors game.card.CardEffect)
# ---------------------------------------------------------------------------

class EffectSchema(BaseModel):
    """
    JSON representation of a single composable card effect.

    Maps directly to game.card.CardEffect after validation.
    The 'type' string must match a value in game.enums.EffectType.

    Validation:
      - 'type' must be a known EffectType value (checked in CardLoader,
        not here, so new effect types don't require a models.py change).
      - 'params' is passed through as-is; individual Effect classes own
        their own param validation at resolve() time.

    Example::

        { "type": "deal_damage", "params": { "amount": 1 } }
        { "type": "set_range_bonus", "params": { "range_bonus": 1 } }
        { "type": "heal", "params": { "amount": 1, "min_players": 2 } }
    """
    type: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Copy schema  (one physical card instance in the deck)
# ---------------------------------------------------------------------------

class CopySchema(BaseModel):
    """
    Describes one physical copy of a card in the deck.

    Each CopySchema produces exactly one Card instance with the given
    suit and rank.  A card with four copies at different suits/ranks
    will appear four times in the shuffled deck.

    'suit' must match a CardSuit value: spades / hearts / diamonds / clubs
    'rank' must match a CardRank value: 2–10 / J / Q / K / A

    Example::

        { "suit": "spades", "rank": "A" }
    """
    suit: str = Field(..., min_length=1)
    rank: str = Field(..., min_length=1)

    model_config = {"extra": "forbid"}

    @field_validator("suit")
    @classmethod
    def suit_must_be_valid(cls, v: str) -> str:
        valid = {"spades", "hearts", "diamonds", "clubs"}
        if v.lower() not in valid:
            raise ValueError(f"Invalid suit {v!r}. Must be one of: {sorted(valid)}")
        return v.lower()

    @field_validator("rank")
    @classmethod
    def rank_must_be_valid(cls, v: str) -> str:
        valid = {"2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"}
        if v.upper() not in valid:
            raise ValueError(f"Invalid rank {v!r}. Must be one of: {sorted(valid)}")
        return v.upper()


# ---------------------------------------------------------------------------
# Card schema  (one card definition — maps to CardDefinition)
# ---------------------------------------------------------------------------

class CardSchema(BaseModel):
    """
    JSON schema for one card definition.

    After validation this is converted to a game.card.CardDefinition by
    CardLoader._schema_to_definition().

    Required fields:
        id          Unique identifier within the pack.  Must be a valid
                    Python identifier (letters, digits, underscores).
                    Collisions across packs are resolved by load order.
        name        Human-readable display name (default locale).
        card_type   "brown" | "blue" | "green" | "character"
        copies      At least one copy must be defined.

    Optional fields:
        description     Tooltip / flavour text.  Default: "".
        effects         Effects fired when the card is played.  Default: [].
        on_equip        Effects applied when a BLUE card enters equipment.
        on_unequip      Effects applied when a BLUE card leaves equipment.
        tags            Free-form strings.  e.g. ["weapon", "attack"].
        max_range       Attack cards only.  None = unlimited.
        min_range       Default 1.
        asset_name      Filename stem for the card image.
                        Default: same as `id`.  e.g. "bang" → "bang.webp"
        source_pack     Set by the loader; not read from JSON.

    Validation rules:
        - BLUE cards should define on_equip effects (warning only — some
          blue cards are purely passive and have no explicit on_equip).
        - BROWN / GREEN cards should not define on_equip (warning only).
        - max_range must be >= min_range if both are set.
        - copies list must not be empty.
    """
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(..., min_length=1, max_length=64)
    card_type: str = Field(..., min_length=1)
    description: str = Field(default="")
    effects: list[EffectSchema] = Field(default_factory=list)
    on_equip: list[EffectSchema] = Field(default_factory=list)
    on_unequip: list[EffectSchema] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    max_range: int | None = Field(default=None, ge=1)
    min_range: int = Field(default=1, ge=1)
    asset_name: str | None = None      # defaults to `id` if omitted
    copies: list[CopySchema] = Field(..., min_length=1)

    # Set by CardLoader after loading — not present in JSON files.
    source_pack: str = Field(default="unknown")

    model_config = {"extra": "forbid"}

    @field_validator("card_type")
    @classmethod
    def card_type_must_be_valid(cls, v: str) -> str:
        valid = {"brown", "blue", "green", "character"}
        if v.lower() not in valid:
            raise ValueError(f"Invalid card_type {v!r}. Must be one of: {sorted(valid)}")
        return v.lower()

    @field_validator("tags")
    @classmethod
    def tags_must_be_strings(cls, v: list[str]) -> list[str]:
        return [str(t).lower().strip() for t in v]

    @model_validator(mode="after")
    def validate_range_consistency(self) -> "CardSchema":
        if self.max_range is not None and self.max_range < self.min_range:
            raise ValueError(
                f"max_range ({self.max_range}) must be >= min_range ({self.min_range})"
            )
        return self

    @property
    def resolved_asset_name(self) -> str:
        """Filename stem for the card image, defaulting to the card id."""
        return self.asset_name or self.id


# ---------------------------------------------------------------------------
# Pack schema  (one JSON file = one card pack)
# ---------------------------------------------------------------------------

class PackSchema(BaseModel):
    """
    Top-level schema for a card pack JSON file.

    A pack is the unit of card loading.  The base game ships one pack
    (base.json).  Expansions and mods each add their own pack files.

    Fields:
        pack_id     Short identifier.  e.g. "base", "dodge_city", "my_mod"
        pack_name   Human-readable.    e.g. "Base Game", "Dodge City"
        version     Semantic version string.  Informational only for now.
        description Optional flavour text for the mod marketplace.
        author      Mod author name (optional, for mod packs).
        cards       The list of card definitions in this pack.
        requires    Other pack_ids this pack depends on.
                    The loader checks these are already loaded before
                    registering this pack's cards.

    Example::

        {
          "pack_id": "base",
          "pack_name": "Base Game",
          "version": "1.0.0",
          "cards": [ ... ]
        }
    """
    pack_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    pack_name: str = Field(..., min_length=1, max_length=64)
    version: str = Field(default="1.0.0")
    description: str = Field(default="")
    author: str = Field(default="")
    cards: list[CardSchema] = Field(..., min_length=1)
    requires: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}   # forward-compatible: ignore unknown keys

    @model_validator(mode="after")
    def stamp_source_pack(self) -> "PackSchema":
        """
        Set source_pack on every card to this pack's id.
        This happens after full validation so it can't be spoofed from JSON.
        """
        for card in self.cards:
            # Bypass Pydantic's immutability for this one loader-side stamp.
            object.__setattr__(card, "source_pack", self.pack_id)
        return self

    def card_ids(self) -> list[str]:
        return [c.id for c in self.cards]

    def duplicate_ids(self) -> list[str]:
        """Return any card ids that appear more than once within this pack."""
        seen: set[str] = set()
        dupes: list[str] = []
        for card in self.cards:
            if card.id in seen:
                dupes.append(card.id)
            seen.add(card.id)
        return dupes
