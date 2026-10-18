"""
Výdrž game engine. Pure Python: no networking, no event loop.

Flow
  start()           each player gets one character, 8 locations (one of each
                    number 1-8) and 8 existences. All hands are public.
  repeat 8 rounds:
    CHOOSING        every player picks one location + one existence from their
                    hand (hidden from the others until all have picked).
    resolve         3 drink cards are drawn. For each drink, a player's number
                    is the sum of that drink's stat on their character,
                    chosen location and chosen existence. The player with the
                    highest total wins the round and keeps their location; its
                    number is added to their score. All other played cards are
                    discarded. A tie is broken by drawing single drink cards
                    and counting only the tied players' characters.
    REVEAL          result is shown; the host starts the next round.
  FINISHED          highest score wins, lowest loses (ties broken the same way).

Assumptions to confirm with the game designer (kept as named constants/notes):
  * The 8+8 cards are dealt ONCE per game and are used up one pair per round
    (8 rounds, 8 of each). If they are dealt every round, change _deal().
  * Picks are simultaneous and hidden until everyone has chosen.
  * A tie-break repeats until one player is left (capped by MAX_TIEBREAK_DRAWS).
  * The loser tie-break is the mirror of the winner's: lowest character stat loses.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from modes.vydrz.dealing import EXISTENCES_DEALT, deal_hands
from modes.vydrz.loader import VydrzContent
from modes.vydrz.models import CharacterCard, DrinkCard, ExistenceCard, LocationCard
from modes.vydrz.scoring import contribution, leaders

ROUNDS = 8
DRINKS_PER_ROUND = 3
MIN_PLAYERS, MAX_PLAYERS = 2, 7
MAX_TIEBREAK_DRAWS = 30


class Phase(str, Enum):
    LOBBY = "lobby"
    CHOOSING = "choosing"
    REVEAL = "reveal"
    FINISHED = "finished"


@dataclass
class ActionResult:
    ok: bool
    error: str = ""

    @classmethod
    def success(cls) -> "ActionResult":
        return cls(True)

    @classmethod
    def fail(cls, reason: str) -> "ActionResult":
        return cls(False, reason)


@dataclass
class _Player:
    player_id: str
    display_name: str
    character: CharacterCard
    locations: list[LocationCard] = field(default_factory=list)
    existences: list[ExistenceCard] = field(default_factory=list)
    won: list[LocationCard] = field(default_factory=list)
    score: int = 0
    pick: tuple[str, str] | None = None        # (location uid, existence uid)
    away: bool = False                         # left the game; the engine plays for them


class VydrzGame:
    def __init__(
        self,
        player_ids: list[str],
        content: VydrzContent,
        display_names: dict[str, str] | None = None,
        seed: int | None = None,
        rounds: int = ROUNDS,
    ) -> None:
        self.content = content
        self.rounds_total = rounds
        self.seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
        # The one RNG. It is only ever used to shuffle (see _shuffled).
        self._rng = random.Random(self.seed)
        self._player_ids = list(player_ids)
        self._names = display_names or {}
        self.phase = Phase.LOBBY
        self.round = 0
        self.players: list[_Player] = []
        self._drink_deck: list[DrinkCard] = []
        self._drink_discard: list[DrinkCard] = []
        self._location_pool_left = 0
        self._existence_pool_left = 0
        self.last_result: dict | None = None

    # ------------------------------------------------------------------ setup

    def _shuffled(self, items: list) -> list:
        out = list(items)
        self._rng.shuffle(out)
        return out

    def start(self) -> ActionResult:
        if self.phase != Phase.LOBBY:
            return ActionResult.fail("Hra už začala.")
        n = len(self._player_ids)
        if not MIN_PLAYERS <= n <= MAX_PLAYERS:
            return ActionResult.fail(f"Potřeba {MIN_PLAYERS}–{MAX_PLAYERS} hráčů, je jich {n}.")
        err = self._deal()
        if err:
            return ActionResult.fail(err)
        self.phase = Phase.CHOOSING
        self.round = 1
        return ActionResult.success()

    def _deal(self) -> str:
        deal = deal_hands(self.content, len(self._player_ids), self._shuffled)
        if isinstance(deal, str):
            return deal
        self.players = [
            _Player(pid, self._names.get(pid, pid), deal.characters[i],
                    locations=deal.locations[i], existences=deal.existences[i])
            for i, pid in enumerate(self._player_ids)
        ]
        self._location_pool_left = deal.locations_left
        self._existence_pool_left = deal.existences_left
        self._drink_deck = deal.drink_deck
        return ""

    # ---------------------------------------------------------------- actions

    def action_choose(self, player_id: str, location_uid: str, existence_uid: str) -> ActionResult:
        """Pick (or change the pick of) one location + one existence."""
        if self.phase != Phase.CHOOSING:
            return ActionResult.fail("Teď se nevybírá.")
        p = self._player(player_id)
        if p is None:
            return ActionResult.fail("Neznámý hráč.")
        if not any(l.uid == location_uid for l in p.locations):
            return ActionResult.fail("Tuhle lokaci nemáš.")
        if not any(e.uid == existence_uid for e in p.existences):
            return ActionResult.fail("Tuhle existenci nemáš.")
        p.pick = (location_uid, existence_uid)
        self._resolve_if_complete()
        return ActionResult.success()

    def mark_away(self, player_id: str) -> None:
        """A player left. The game never waits for them: their picks are made automatically."""
        p = self._player(player_id)
        if p is None or p.away:
            return
        p.away = True
        self._autoplay_away()

    def _autoplay_away(self) -> None:
        if self.phase != Phase.CHOOSING:
            return
        for p in self.players:
            if p.away and p.pick is None and p.locations and p.existences:
                p.pick = (p.locations[0].uid, p.existences[0].uid)
        self._resolve_if_complete()

    def _resolve_if_complete(self) -> None:
        # The round resolves only when EVERY player has picked.
        if self.phase == Phase.CHOOSING and all(q.pick for q in self.players):
            self._resolve_round()

    def action_next_round(self) -> ActionResult:
        if self.phase != Phase.REVEAL:
            return ActionResult.fail("Kolo ještě není vyhodnocené.")
        self.round += 1
        self.phase = Phase.CHOOSING
        for p in self.players:
            p.pick = None
        self._autoplay_away()
        return ActionResult.success()

    # ------------------------------------------------------------- resolution

    def _draw_drink(self) -> DrinkCard:
        if not self._drink_deck:
            self._drink_deck = self._shuffled(self._drink_discard)
            self._drink_discard = []
        card = self._drink_deck.pop()
        self._drink_discard.append(card)
        return card

    def _picked(self, p: _Player) -> tuple[LocationCard, ExistenceCard]:
        loc_uid, ex_uid = p.pick  # type: ignore[misc]
        return (next(l for l in p.locations if l.uid == loc_uid),
                next(e for e in p.existences if e.uid == ex_uid))

    def _break_tie(self, tied: list[str], *, highest: bool) -> tuple[list[str], list[dict]]:
        """Draw single drinks counting only characters until one player is left."""
        steps: list[dict] = []
        while len(tied) > 1 and len(steps) < MAX_TIEBREAK_DRAWS:
            drink = self._draw_drink().drink
            values = {pid: self._player(pid).character.stats[drink] for pid in tied}
            tied = leaders(values, highest=highest)
            steps.append({"drink": drink, "values": values, "remaining": list(tied)})
        return tied, steps

    def _resolve_round(self) -> None:
        picks = {p.player_id: self._picked(p) for p in self.players}
        drawn = [self._draw_drink().drink for _ in range(DRINKS_PER_ROUND)]

        rows = []
        totals = {p.player_id: 0 for p in self.players}
        for drink in drawn:
            per_player = {}
            for p in self.players:
                loc, ex = picks[p.player_id]
                c = contribution(drink, p.character, loc, ex)
                per_player[p.player_id] = c.to_dict()
                totals[p.player_id] += c.total
            rows.append({"drink": drink, "players": per_player})

        tied = leaders(totals, highest=True)
        remaining, tiebreak = self._break_tie(tied, highest=True)
        winner_id = remaining[0] if len(remaining) == 1 else None

        scores_before = {p.player_id: p.score for p in self.players}
        awarded = 0
        if winner_id:
            winner = self._player(winner_id)
            loc, _ = picks[winner_id]
            winner.won.append(loc)
            winner.score += loc.number
            awarded = loc.number
        # Played cards leave every hand (the winner's location moved to "won").
        for p in self.players:
            loc, ex = picks[p.player_id]
            p.locations = [l for l in p.locations if l.uid != loc.uid]
            p.existences = [e for e in p.existences if e.uid != ex.uid]

        self.last_result = {
            "round": self.round,
            "drawn": rows,
            "totals": totals,
            "picks": {pid: {"lokace": l.to_dict(), "existence": e.to_dict()} for pid, (l, e) in picks.items()},
            "tiebreak": tiebreak,
            "winner_id": winner_id,
            "awarded": awarded,
            "scores_before": scores_before,
            "scores_after": {p.player_id: p.score for p in self.players},
            "final": None,
        }
        if self.round >= self.rounds_total:
            self.last_result["final"] = self._finish()
            self.phase = Phase.FINISHED
        else:
            self.phase = Phase.REVEAL

    def _finish(self) -> dict:
        scores = {p.player_id: p.score for p in self.players}
        first_group = leaders(scores, highest=True)
        winners, first_steps = self._break_tie(first_group, highest=True)
        winner_id = winners[0] if len(winners) == 1 else None
        last_group = [pid for pid in leaders(scores, highest=False) if pid != winner_id]
        losers, last_steps = self._break_tie(last_group, highest=False) if last_group else ([], [])
        loser_id = losers[0] if len(losers) == 1 else None
        ranking = sorted(
            scores,
            key=lambda pid: (-scores[pid], pid != winner_id, pid == loser_id),
        )
        return {
            "winner_id": winner_id,
            "loser_id": loser_id,
            "ranking": [{"player_id": pid, "score": scores[pid], "place": i + 1}
                        for i, pid in enumerate(ranking)],
            "tiebreak_first": first_steps,
            "tiebreak_last": last_steps,
        }

    # --------------------------------------------------------------- snapshot

    def _player(self, player_id: str) -> _Player | None:
        return next((p for p in self.players if p.player_id == player_id), None)

    def snapshot(self, viewer_id: str) -> dict[str, Any]:
        """
        Full state for one viewer. Hands are public by the rules; the only
        hidden information is who picked what until the round resolves.
        """
        def player_dict(p: _Player) -> dict:
            own_or_open = p.player_id == viewer_id or self.phase != Phase.CHOOSING
            return {
                "player_id": p.player_id,
                "display_name": p.display_name,
                "score": p.score,
                "character": p.character.to_dict(),
                "locations": [l.to_dict() for l in p.locations],
                "existences": [e.to_dict() for e in p.existences],
                "won": [l.to_dict() for l in p.won],
                "locked": p.pick is not None,
                "away": p.away,
                "pick": ({"location_uid": p.pick[0], "existence_uid": p.pick[1]}
                         if p.pick and own_or_open else None),
            }

        return {
            "mode": "vydrz",
            "phase": self.phase.value,
            "round": self.round,
            "rounds_total": self.rounds_total,
            "you": viewer_id,
            "players": [player_dict(p) for p in self.players],
            "decks": {
                "chlast": len(self._drink_deck),
                "chlast_discard": len(self._drink_discard),
                "lokace": self._location_pool_left,
                "existence": self._existence_pool_left,
                "postava": len(self.content.characters) - len(self.players),
            },
            "last_result": self.last_result,
        }
