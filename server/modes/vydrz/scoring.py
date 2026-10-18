"""
Pure scoring functions for Výdrž (no state, no randomness).

A player's number for one drink is the sum of that drink's stat on every card
they have in play: their character, and the location and existence they chose.
Locations and existences only print 2-3 drinks, so for most drinks they add
nothing (None here, so the UI can tell "no stat" from a printed 0).
"""
from __future__ import annotations

from dataclasses import dataclass

from modes.vydrz.models import CharacterCard, ExistenceCard, LocationCard


@dataclass(frozen=True)
class Contribution:
    postava: int
    lokace: int | None
    existence: int | None

    @property
    def total(self) -> int:
        return self.postava + (self.lokace or 0) + (self.existence or 0)

    def to_dict(self) -> dict:
        return {"postava": self.postava, "lokace": self.lokace,
                "existence": self.existence, "total": self.total}


def contribution(drink: str, character: CharacterCard,
                 location: LocationCard | None, existence: ExistenceCard | None) -> Contribution:
    return Contribution(
        postava=character.stats[drink],
        lokace=location.stats.get(drink) if location else None,
        existence=existence.stats.get(drink) if existence else None,
    )


def leaders(values: dict[str, int], *, highest: bool = True) -> list[str]:
    """Player ids holding the best value (ties keep every holder)."""
    best = (max if highest else min)(values.values())
    return [pid for pid, v in values.items() if v == best]
