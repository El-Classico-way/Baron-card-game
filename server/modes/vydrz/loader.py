"""Load and validate Výdrž card data (JSON), and mint card instances."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from modes.vydrz.models import (
    DRINKS, LOCATION_NUMBERS, STAT_MAX, STAT_MIN,
    CharacterCard, DrinkCard, DrinkInfo, ExistenceCard, LocationCard,
)

DEFAULT_PATH = Path(__file__).parent / "data" / "vydrz.json"


class ContentError(ValueError):
    """The card data file is malformed."""


@dataclass(frozen=True)
class VydrzContent:
    """Validated card definitions. Instances (with uids) are made per game."""
    drinks: tuple[DrinkInfo, ...]
    drink_copies: int
    characters: tuple[dict, ...]
    locations: tuple[dict, ...]
    existences: tuple[dict, ...]

    def drink_name(self, drink: str) -> str:
        return next(d.name for d in self.drinks if d.id == drink)

    # Fresh instances; uids are unique within one game.
    def make_characters(self) -> list[CharacterCard]:
        return [CharacterCard(uid=f"c-{c['id']}", id=c["id"], name=c["name"], stats=dict(c["stats"]))
                for c in self.characters]

    def make_locations(self) -> list[LocationCard]:
        return [LocationCard(uid=f"l-{c['id']}", id=c["id"], number=c["number"], name=c["name"],
                             description=c.get("description", ""), stats=dict(c["stats"]))
                for c in self.locations]

    def make_existences(self) -> list[ExistenceCard]:
        return [ExistenceCard(uid=f"e-{c['id']}", id=c["id"], name=c["name"],
                              description=c.get("description", ""), stats=dict(c["stats"]))
                for c in self.existences]

    def make_drinks(self) -> list[DrinkCard]:
        return [DrinkCard(uid=f"d-{d}-{i}", drink=d)
                for d in DRINKS for i in range(self.drink_copies)]

    def to_public_dict(self) -> dict:
        return {"drinks": [{"id": d.id, "name": d.name} for d in self.drinks]}


def _check_stats(where: str, stats: dict, *, complete: bool, size: tuple[int, int] | None = None) -> None:
    if not isinstance(stats, dict):
        raise ContentError(f"{where}: stats must be an object")
    for drink, value in stats.items():
        if drink not in DRINKS:
            raise ContentError(f"{where}: unknown drink {drink!r}")
        if not isinstance(value, int) or isinstance(value, bool) or not STAT_MIN <= value <= STAT_MAX:
            raise ContentError(f"{where}: {drink} must be an int in {STAT_MIN}..{STAT_MAX}, got {value!r}")
    if complete and set(stats) != set(DRINKS):
        raise ContentError(f"{where}: needs a stat for every drink")
    if size and not size[0] <= len(stats) <= size[1]:
        raise ContentError(f"{where}: needs {size[0]}-{size[1]} stats, has {len(stats)}")


def parse_content(data: dict, source: str = "<data>") -> VydrzContent:
    try:
        drinks = tuple(DrinkInfo(d["id"], d["name"]) for d in data["drinks"])
        if tuple(d.id for d in drinks) != DRINKS:
            raise ContentError(f"{source}: drinks must be exactly {list(DRINKS)} in that order")
        for c in data["characters"]:
            _check_stats(f"character {c['id']}", c["stats"], complete=True)
        for c in data["locations"]:
            if c["number"] not in LOCATION_NUMBERS:
                raise ContentError(f"location {c['id']}: number must be 1-8")
            _check_stats(f"location {c['id']}", c["stats"], complete=False, size=(2, 3))
        for c in data["existences"]:
            _check_stats(f"existence {c['id']}", c["stats"], complete=False, size=(2, 3))
        ids = [c["id"] for k in ("characters", "locations", "existences") for c in data[k]]
        if len(ids) != len(set(ids)):
            raise ContentError(f"{source}: duplicate card ids")
        return VydrzContent(
            drinks=drinks,
            drink_copies=int(data.get("drink_copies", 10)),
            characters=tuple(data["characters"]),
            locations=tuple(data["locations"]),
            existences=tuple(data["existences"]),
        )
    except (KeyError, TypeError) as exc:
        raise ContentError(f"{source}: missing or malformed field ({exc!r})") from exc


def load_content(path: str | Path) -> VydrzContent:
    path = Path(path)
    return parse_content(json.loads(path.read_text(encoding="utf-8")), str(path))


def load_default_content() -> VydrzContent:
    return load_content(DEFAULT_PATH)
