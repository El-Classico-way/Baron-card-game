"""
server/game/state.py

GameState — the single source of truth for all authoritative game state.

WHY A DEDICATED STATE OBJECT:
  Separating state from the Game class (which owns logic/flow) means:
    1. State can be serialised / snapshotted without touching logic.
    2. Tests can construct arbitrary states directly without running Game.
    3. The networking layer can read state for snapshots without coupling
       to the Game class.
    4. Future: state can be persisted to a store for crash recovery.

  The Game class holds a GameState and mutates it.  Nothing else mutates it.

  WHY NO PYDANTIC HERE:
    Same reason as Player — state mutates constantly inside the engine.
    Pydantic validation belongs at the network boundary (incoming actions).
    We snapshot to plain dicts for the networking layer.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from game.deck import Deck
from game.player import Player
from game.enums import GamePhase, TurnPhase, Role


@dataclass
class TurnState:
    """
    Tracks everything specific to the current player's turn.

    Kept as a nested object so turn data can be reset atomically by
    replacing the whole object rather than remembering to clear each field.
    """
    player_id: str
    turn_number: int
    phase: TurnPhase = TurnPhase.DRAW
    cards_drawn: int = 0
    started_at: float = field(default_factory=time.time)

    # TODO: add pending_reaction: ReactionContext | None = None
    #       when the interrupt/reaction system is designed.

    def advance_phase(self) -> None:
        """Move to the next turn phase in order."""
        order = [TurnPhase.DRAW, TurnPhase.PLAY, TurnPhase.DISCARD, TurnPhase.END]
        idx = order.index(self.phase)
        if idx < len(order) - 1:
            self.phase = order[idx + 1]

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "turn_number": self.turn_number,
            "phase": self.phase.value,
            "cards_drawn": self.cards_drawn,
        }


@dataclass
class GameState:
    """
    Complete authoritative game state for one session.

    The networking layer reads this to build state snapshots.
    The Game class (game.py) is the only writer.

    Fields:
        game_id         Unique identifier for this session.
        players         Ordered list of players (turn order).
        deck            The shared draw/discard deck.
        phase           Top-level game lifecycle phase.
        turn            Current turn state (None before first turn).
        turn_number     Monotonically increasing turn counter.
        round_number    Increments each time the turn order completes a cycle.
        seed            RNG seed used for this session (for reproducibility).
        mode            Game mode identifier (e.g. "classic", "high_noon").
        winner_ids      Populated when the game ends.
        started_at      Unix timestamp of game start.

    Usage::

        state = GameState.new(player_ids=["p1","p2","p3"], seed=42)
        # passed to Game(state=state, ...)
    """

    game_id: str
    players: list[Player]
    deck: Deck
    phase: GamePhase = GamePhase.LOBBY
    turn: TurnState | None = None
    turn_number: int = 0
    round_number: int = 0
    seed: int | None = None
    mode: str = "classic"
    winner_ids: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Player lookups
    # ------------------------------------------------------------------

    def get_player(self, player_id: str) -> Player:
        """Raises KeyError if player not found."""
        for p in self.players:
            if p.player_id == player_id:
                return p
        raise KeyError(f"Player {player_id!r} not in game {self.game_id!r}.")

    def alive_players(self) -> list[Player]:
        from game.enums import PlayerStatus
        return [p for p in self.players if p.status == PlayerStatus.ALIVE]

    def dead_players(self) -> list[Player]:
        from game.enums import PlayerStatus
        return [p for p in self.players if p.status == PlayerStatus.DEAD]

    def get_sheriff(self) -> Player | None:
        for p in self.players:
            if p.role == Role.SHERIFF:
                return p
        return None

    # ------------------------------------------------------------------
    # Turn order navigation
    # ------------------------------------------------------------------

    def current_player(self) -> Player | None:
        """The player whose turn it currently is."""
        if self.turn is None:
            return None
        return self.get_player(self.turn.player_id)

    def next_alive_player(self, after_id: str) -> Player | None:
        """
        Return the next alive player in turn order after the given player.
        Wraps around the list.  Returns None if no alive players remain.
        """
        alive = self.alive_players()
        if not alive:
            return None

        ids = [p.player_id for p in self.players]   # full turn order
        if after_id not in ids:
            return alive[0]

        start = ids.index(after_id)
        n = len(ids)
        for i in range(1, n + 1):
            candidate_id = ids[(start + i) % n]
            candidate = self.get_player(candidate_id)
            if candidate.is_alive:
                return candidate
        return None

    # ------------------------------------------------------------------
    # Range calculation
    # ------------------------------------------------------------------

    def range_between(self, source_id: str, target_id: str) -> int:
        """
        Compute the effective range from source to target.

        Bang! uses circular seating: range is the minimum of the clockwise
        and counter-clockwise distance, adjusted by equipment bonuses.

        Adjustment:
          effective_range = base_distance
                          + target.defense_range_bonus
                          - source.attack_range_bonus

        (Attack bonus shrinks effective range; defense bonus increases it.)
        """
        alive = [p.player_id for p in self.alive_players()]
        if source_id not in alive or target_id not in alive:
            return 999   # effectively unreachable

        n = len(alive)
        src_idx = alive.index(source_id)
        tgt_idx = alive.index(target_id)

        clockwise = (tgt_idx - src_idx) % n
        counter   = (src_idx - tgt_idx) % n
        base_distance = min(clockwise, counter)

        source  = self.get_player(source_id)
        target  = self.get_player(target_id)

        return (
            base_distance
            + target.defense_range_bonus
            - source.attack_range_bonus
        )

    # ------------------------------------------------------------------
    # Role helpers (win condition queries)
    # ------------------------------------------------------------------

    def players_with_role(self, role: Role) -> list[Player]:
        return [p for p in self.players if p.role == role]

    def alive_players_with_role(self, role: Role) -> list[Player]:
        return [p for p in self.alive_players() if p.role == role]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_public_dict(self) -> dict:
        """
        Full public snapshot — safe to broadcast to all connected clients.
        Private player hands are excluded; the networking layer adds them
        per-client from player.to_private_dict().
        """
        return {
            "game_id": self.game_id,
            "phase": self.phase.value,
            "mode": self.mode,
            "turn_number": self.turn_number,
            "round_number": self.round_number,
            "turn": self.turn.to_dict() if self.turn else None,
            "players": [p.to_public_dict() for p in self.players],
            "deck": self.deck.to_public_dict(),
            "winner_ids": self.winner_ids,
        }

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def new(
        cls,
        player_ids: list[str],
        display_names: dict[str, str] | None = None,
        seed: int | None = None,
        mode: str = "classic",
    ) -> "GameState":
        """
        Construct a fresh GameState for a new game session.

        Max HP values are set to defaults here; the Game class adjusts the
        Sheriff's HP after role assignment.

        Args:
            player_ids:    Ordered list of player ids (determines turn order).
            display_names: Optional mapping of player_id → display name.
            seed:          RNG seed (random if omitted).
            mode:          Game mode identifier.
        """
        import random as _random
        resolved_seed = seed if seed is not None else _random.randint(0, 2**31)
        names = display_names or {}

        players = [
            Player(
                player_id=pid,
                display_name=names.get(pid, pid),
                max_hp=4,   # default; adjusted by game mode after role assignment
            )
            for pid in player_ids
        ]

        return cls(
            game_id=uuid.uuid4().hex,
            players=players,
            deck=Deck(seed=resolved_seed),
            seed=resolved_seed,
            mode=mode,
        )

    def __repr__(self) -> str:
        return (
            f"GameState(id={self.game_id[:8]}, phase={self.phase}, "
            f"players={len(self.players)}, turn={self.turn_number})"
        )
