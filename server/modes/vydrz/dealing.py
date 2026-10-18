"""
Initial deal shared by the Výdrž variants (automatic and manual).

Each player gets one character, one location of every number 1-8 and
EXISTENCES_DEALT existences. `shuffle` is the caller's single RNG hook, so a
seeded game deals the same way every time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from modes.vydrz.loader import VydrzContent
from modes.vydrz.models import (
    LOCATION_NUMBERS, CharacterCard, DrinkCard, ExistenceCard, LocationCard,
)

EXISTENCES_DEALT = 8


@dataclass
class Deal:
    characters: list[CharacterCard]            # one per player, in player order
    locations: list[list[LocationCard]]        # per player, numbers 1..8
    existences: list[list[ExistenceCard]]      # per player
    locations_left: int
    existences_left: int
    drink_deck: list[DrinkCard]


def deal_hands(content: VydrzContent, player_count: int,
               shuffle: Callable[[list], list]) -> Deal | str:
    """Return the deal, or a Czech error message if the card pool is too small."""
    n = player_count
    characters = shuffle(content.make_characters())
    if len(characters) < n:
        return "Málo postav pro tento počet hráčů."
    by_number: dict[int, list[LocationCard]] = {k: [] for k in LOCATION_NUMBERS}
    for loc in content.make_locations():
        by_number[loc.number].append(loc)
    if any(len(v) < n for v in by_number.values()):
        return "Málo lokací jednoho z čísel pro tento počet hráčů."
    existences = shuffle(content.make_existences())
    if len(existences) < n * EXISTENCES_DEALT:
        return "Málo existencí pro tento počet hráčů."

    locations: list[list[LocationCard]] = [[] for _ in range(n)]
    locations_left = 0
    for number in LOCATION_NUMBERS:
        pile = shuffle(by_number[number])
        for i in range(n):
            locations[i].append(pile[i])
        locations_left += len(pile) - n
    return Deal(
        characters=characters[:n],
        locations=locations,
        existences=[existences[i * EXISTENCES_DEALT:(i + 1) * EXISTENCES_DEALT] for i in range(n)],
        locations_left=locations_left,
        existences_left=len(existences) - n * EXISTENCES_DEALT,
        drink_deck=shuffle(content.make_drinks()),
    )
