"""
Game modes. Each mode is a self-contained rule set that reuses the shared
infrastructure (rooms, networking, card loading) but owns its own state,
phases and scoring.

  rezba   Bang-like round-based action game. NOT IMPLEMENTED as a real mode;
          the older prototype engine in server/game is kept for reference.
  vydrz   Drink-stat scoring game (server/modes/vydrz).
"""
from __future__ import annotations

# id -> display info. "playable" tells clients whether to offer the mode.
MODES: dict[str, dict] = {
    "rezba": {"name": "Řežba", "playable": False},
    "vydrz": {"name": "Výdrž", "playable": True},
}
DEFAULT_MODE = "vydrz"
