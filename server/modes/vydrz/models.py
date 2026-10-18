"""
Card models for Výdrž. Plain immutable data; no rules in here.

Card kinds (tags as printed on the cards):
  #postava     character. One per player; has a stat for every drink.
  #chlast      drink card. Its face is one of the ten drink kinds.
  #lokace      location. Number 1-8, name, description, stats for 2-3 drinks.
  #existence   like a location without a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The ten drink kinds, in display order. A stat is keyed by one of these ids.
DRINKS: tuple[str, ...] = (
    "pivo", "vino", "jahudka", "gin", "rum", "slivka", "vodka", "cider", "tatran", "jager",
)

STAT_MIN, STAT_MAX = -3, 3
LOCATION_NUMBERS: tuple[int, ...] = tuple(range(1, 9))


@dataclass(frozen=True)
class CharacterCard:
    uid: str
    id: str
    name: str
    stats: dict[str, int]          # every drink -> int

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": "postava", "id": self.id, "name": self.name,
                "stats": dict(self.stats)}


@dataclass(frozen=True)
class DrinkCard:
    uid: str
    drink: str

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": "chlast", "drink": self.drink}


@dataclass(frozen=True)
class LocationCard:
    uid: str
    id: str
    number: int
    name: str
    description: str
    stats: dict[str, int]          # only the 2-3 drinks printed on the card

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": "lokace", "id": self.id, "number": self.number,
                "name": self.name, "description": self.description, "stats": dict(self.stats)}


@dataclass(frozen=True)
class ExistenceCard:
    uid: str
    id: str
    name: str
    description: str
    stats: dict[str, int]

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": "existence", "id": self.id,
                "name": self.name, "description": self.description, "stats": dict(self.stats)}


@dataclass(frozen=True)
class DrinkInfo:
    id: str
    name: str
