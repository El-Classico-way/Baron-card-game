"""
Game modes. Each mode is a self-contained rule set that reuses the shared
infrastructure (rooms, networking, card loading) but owns its own state,
phases and scoring.

  rezba   Bang-like round-based action game. NOT IMPLEMENTED as a real mode;
          the older prototype engine in server/game is kept for reference.
  vydrz        Drink-stat scoring game, counted automatically (server/modes/vydrz).
  vydrz_rucne  Same game, counted by the players (server/modes/vydrz_manual).
"""
from __future__ import annotations

# id -> display info. "playable" tells clients whether to offer the mode.
# Modes with the same "family" are variants of one game (shown as a sub-choice).
MODES: dict[str, dict] = {
    "rezba": {"name": "Řežba", "family": "rezba", "variant": "", "playable": False},
    "vydrz": {"name": "Výdrž", "family": "vydrz", "variant": "Automaticky", "playable": True},
    "vydrz_rucne": {"name": "Výdrž", "family": "vydrz", "variant": "Ručně", "playable": True},
}
DEFAULT_MODE = "vydrz"
