"""Shared fixtures: build a started Game with a recording EventBus."""
from __future__ import annotations

import pytest

from cards import load_default_registry
from effects import build_default_registry
from game.events import EventBus
from game.game import Game
from game.state import GameState


class Harness:
    def __init__(self, n: int, seed: int) -> None:
        self.ids = [f"p{i + 1}" for i in range(n)]
        self.state = GameState.new(player_ids=self.ids, seed=seed)
        self.bus = EventBus()
        self.events: list = []
        self.bus.subscribe_all(self.events.append)
        self.game = Game(self.state, self.bus, registry=build_default_registry())

    def start(self):
        return self.game.start(load_default_registry().build_deck())

    @property
    def current(self):
        return self.state.get_player(self.state.turn.player_id)

    def events_of(self, name: str) -> list:
        return [e for e in self.events if type(e).__name__ == name]


@pytest.fixture
def make_game():
    def _make(n: int = 4, seed: int = 42, start: bool = True) -> Harness:
        h = Harness(n, seed)
        if start:
            assert h.start().ok
        return h

    return _make
