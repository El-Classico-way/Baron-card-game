"""
Výdrž (ruční) engine. Pure Python, no networking.

This is a separate variant, not a switch inside the automatic engine
(server/modes/vydrz/game.py), so changes here never touch the automatic one.
It shares only the card models, loader, dealing and the pure scoring helpers.

Flow
  start()        same deal as the automatic mode (character, 8 locations, 8
                 existences, all public).
  PLANNING       each player arranges ALL 8 (location, existence) pairs up
                 front: pair k is used in round k. The order stays secret.
  repeat 8 rounds:
    CALCULATING  3 drink cards are drawn for everybody. Each player sees their
                 own pair for this round and must work out their number
                 (sum of the three drinks' stats on character + location +
                 existence) and type it in. The server knows the right number:
                   equal  -> counted as entered
                   lower  -> counted as entered, tagged "blbec"
                   higher -> counted as -50, tagged "svindlir"
                 Entries are public as soon as they are made.
    when all have entered: the highest value wins the round and keeps the
    location (its number is added to their score). A tie is broken manually
    too: one drink card is drawn, the tied players count only their character,
    and it repeats until one is left.
    REVEAL       result shown; the host starts the next round.
  FINISHED       highest score wins, lowest loses; ties at either end use the
                 same manual single-drink tie-break (highest/lowest).

Assumptions to confirm with the game designer:
  * A "blbec" entry counts with the (lower) number the player typed.
  * Each round has its own three drink cards.
  * A single entry per player and session; no corrections.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from modes.vydrz.dealing import deal_hands
from modes.vydrz.loader import VydrzContent
from modes.vydrz.models import CharacterCard, DrinkCard, ExistenceCard, LocationCard
from modes.vydrz.scoring import contribution, leaders

ROUNDS = 8
DRINKS_PER_ROUND = 3
MIN_PLAYERS, MAX_PLAYERS = 2, 7
MAX_TIEBREAK_DRAWS = 30
MAX_ENTRY = 200                      # sanity bound on a typed number
CHEATER_VALUE = -50                  # what a "svindlir" entry counts as

TAG_BLBEC = "blbec"
TAG_SVINDLIR = "svindlir"


class Phase(str, Enum):
    LOBBY = "lobby"
    PLANNING = "planning"
    CALCULATING = "calculating"
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
    plan: list[tuple[str, str]] | None = None      # 8 (location uid, existence uid)
    away: bool = False                             # left the game; the engine plays for them


@dataclass
class _Entry:
    entered: int
    value: int                                      # what counts (CHEATER_VALUE for a cheater)
    tag: str | None


@dataclass
class _Calc:
    """One manual counting session: a round, or a single-drink tie-break."""
    purpose: str                                    # "round" | "final_first" | "final_last"
    drinks: list[str]
    participants: list[str]
    correct: dict[str, int]
    is_tiebreak: bool
    draw_id: int
    entries: dict[str, _Entry] = field(default_factory=dict)


class ManualVydrzGame:
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
        self._rng = random.Random(self.seed)          # only used to shuffle
        self._player_ids = list(player_ids)
        self._names = display_names or {}
        self.phase = Phase.LOBBY
        self.round = 0
        self.players: list[_Player] = []
        self._drink_deck: list[DrinkCard] = []
        self._drink_discard: list[DrinkCard] = []
        self._pool = {"lokace": 0, "existence": 0}
        self._calc: _Calc | None = None
        self._draw_counter = 0
        self._steps: list[dict] = []                  # finished sessions of the current resolution
        self._final: dict | None = None
        self._round_ctx: dict | None = None           # facts about the round being resolved
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
        deal = deal_hands(self.content, n, self._shuffled)
        if isinstance(deal, str):
            return ActionResult.fail(deal)
        self.players = [
            _Player(pid, self._names.get(pid, pid), deal.characters[i],
                    locations=deal.locations[i], existences=deal.existences[i])
            for i, pid in enumerate(self._player_ids)
        ]
        self._pool = {"lokace": deal.locations_left, "existence": deal.existences_left}
        self._drink_deck = deal.drink_deck
        self.phase = Phase.PLANNING
        self.round = 0
        return ActionResult.success()

    # ---------------------------------------------------------------- actions

    def action_set_plan(self, player_id: str, pairs: list[tuple[str, str]]) -> ActionResult:
        """Set (or change, until everyone is done) the 8 pairs in round order."""
        if self.phase != Phase.PLANNING:
            return ActionResult.fail("Teď se neplánuje.")
        p = self._player(player_id)
        if p is None:
            return ActionResult.fail("Neznámý hráč.")
        if len(pairs) != self.rounds_total:
            return ActionResult.fail(f"Potřeba přesně {self.rounds_total} dvojic.")
        locs = [l for l, _ in pairs]
        exs = [e for _, e in pairs]
        if sorted(locs) != sorted(l.uid for l in p.locations) or sorted(exs) != sorted(e.uid for e in p.existences):
            return ActionResult.fail("Každou svou lokaci a existenci musíš použít přesně jednou.")
        p.plan = [(l, e) for l, e in pairs]
        self._start_if_planned()
        return ActionResult.success()

    def mark_away(self, player_id: str) -> None:
        """A player left. The game never waits for them: the engine plays on their behalf."""
        p = self._player(player_id)
        if p is None or p.away:
            return
        p.away = True
        self._autoplay_away()

    def _start_if_planned(self) -> None:
        # Round 1 begins only when EVERY player has handed in a plan.
        if self.phase == Phase.PLANNING and all(q.plan for q in self.players):
            self.round = 1
            self._begin_round()

    def _autoplay_away(self) -> None:
        """Plan in hand order and enter the right number for players who are away."""
        if self.phase == Phase.PLANNING:
            for p in self.players:
                if p.away and p.plan is None:
                    p.plan = [(l.uid, e.uid) for l, e in zip(p.locations, p.existences)]
            self._start_if_planned()
            return
        c = self._calc
        while self.phase == Phase.CALCULATING and c is not None:
            todo = [pid for pid in c.participants if pid not in c.entries and self._player(pid).away]
            if not todo:
                return
            self.action_submit(todo[0], c.correct[todo[0]])
            if self._calc is not c:                  # the session moved on (round over / tie-break)
                c = self._calc

    def action_submit(self, player_id: str, value: int) -> ActionResult:
        """Enter the number you counted."""
        c = self._calc
        if self.phase != Phase.CALCULATING or c is None:
            return ActionResult.fail("Teď se nepočítá.")
        if player_id not in c.participants:
            return ActionResult.fail("V tomto počítání nejsi.")
        if player_id in c.entries:
            return ActionResult.fail("Už jsi číslo zadal(a).")
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_ENTRY:
            return ActionResult.fail("Neplatné číslo.")
        correct = c.correct[player_id]
        if value == correct:
            c.entries[player_id] = _Entry(value, value, None)
        elif value < correct:
            c.entries[player_id] = _Entry(value, value, TAG_BLBEC)
        else:
            c.entries[player_id] = _Entry(value, CHEATER_VALUE, TAG_SVINDLIR)
        if len(c.entries) == len(c.participants):
            self._complete_session()
        return ActionResult.success()

    def action_next_round(self) -> ActionResult:
        if self.phase != Phase.REVEAL:
            return ActionResult.fail("Kolo ještě není vyhodnocené.")
        self.round += 1
        self._begin_round()
        return ActionResult.success()

    # ----------------------------------------------------------------- engine

    def _player(self, player_id: str) -> _Player | None:
        return next((p for p in self.players if p.player_id == player_id), None)

    def _draw_drink(self) -> str:
        if not self._drink_deck:
            self._drink_deck = self._shuffled(self._drink_discard)
            self._drink_discard = []
        card = self._drink_deck.pop()
        self._drink_discard.append(card)
        return card.drink

    def _pair(self, p: _Player, round_no: int) -> tuple[LocationCard, ExistenceCard]:
        loc_uid, ex_uid = p.plan[round_no - 1]  # type: ignore[index]
        return (next(l for l in p.locations if l.uid == loc_uid),
                next(e for e in p.existences if e.uid == ex_uid))

    def _begin_round(self) -> None:
        """Draw this round's three drinks and compute every player's right answer."""
        self.phase = Phase.CALCULATING
        drinks = [self._draw_drink() for _ in range(DRINKS_PER_ROUND)]
        correct = {}
        for p in self.players:
            loc, ex = self._pair(p, self.round)
            correct[p.player_id] = sum(
                contribution(d, p.character, loc, ex).total for d in drinks)
        self._steps = []
        self._round_ctx = {"drawn": drinks, "scores_before": {p.player_id: p.score for p in self.players}}
        self._draw_counter += 1
        self._calc = _Calc("round", drinks, [p.player_id for p in self.players], correct,
                           False, self._draw_counter)
        self._autoplay_away()

    def _start_tiebreak(self, purpose: str, tied: list[str]) -> None:
        drink = self._draw_drink()
        correct = {pid: self._player(pid).character.stats[drink] for pid in tied}
        self._draw_counter += 1
        self._calc = _Calc(purpose, [drink], list(tied), correct, True, self._draw_counter)
        self._autoplay_away()

    def _entry_table(self, c: _Calc) -> dict[str, dict]:
        return {pid: {"value": e.value, "tag": e.tag, "entered": e.entered, "correct": c.correct[pid]}
                for pid, e in c.entries.items()}

    def _complete_session(self) -> None:
        c = self._calc
        assert c is not None
        self._steps.append({"drinks": list(c.drinks), "tiebreak": c.is_tiebreak,
                            "purpose": c.purpose, "table": self._entry_table(c)})
        values = {pid: e.value for pid, e in c.entries.items()}
        left = leaders(values, highest=c.purpose != "final_last")
        tiebreaks = sum(1 for s in self._steps if s["tiebreak"] and s["purpose"] == c.purpose)
        if len(left) > 1 and tiebreaks < MAX_TIEBREAK_DRAWS:
            self._start_tiebreak(c.purpose, left)
            return
        decided = left[0] if len(left) == 1 else None
        self._calc = None
        if c.purpose == "round":
            self._finish_round(decided)
        else:
            assert self._final is not None
            self._final["winner" if c.purpose == "final_first" else "loser"] = decided
            self._final["steps_" + c.purpose] = [s for s in self._steps if s["purpose"] == c.purpose]
            self._advance_final()

    def _finish_round(self, winner_id: str | None) -> None:
        ctx = self._round_ctx or {}
        picks = {p.player_id: self._pair(p, self.round) for p in self.players}
        awarded = 0
        if winner_id:
            w = self._player(winner_id)
            loc = picks[winner_id][0]
            w.won.append(loc)
            w.score += loc.number
            awarded = loc.number
        first = self._steps[0]
        self.last_result = {
            "round": self.round,
            "drawn": list(ctx.get("drawn", [])),
            "picks": {pid: {"lokace": l.to_dict(), "existence": e.to_dict()} for pid, (l, e) in picks.items()},
            "table": first["table"],
            "tiebreak": [s for s in self._steps[1:]],
            "winner_id": winner_id,
            "awarded": awarded,
            "scores_before": ctx.get("scores_before", {}),
            "scores_after": {p.player_id: p.score for p in self.players},
            "final": None,
        }
        # Played cards leave the hands (the winner's location moved to "won").
        for p in self.players:
            loc, ex = picks[p.player_id]
            p.locations = [l for l in p.locations if l.uid != loc.uid]
            p.existences = [e for e in p.existences if e.uid != ex.uid]
        self._steps = []
        if self.round >= self.rounds_total:
            self._final = {}
            self._advance_final()
        else:
            self.phase = Phase.REVEAL

    def _advance_final(self) -> None:
        f = self._final
        assert f is not None
        scores = {p.player_id: p.score for p in self.players}
        if "winner" not in f:
            group = leaders(scores, highest=True)
            if len(group) == 1:
                f["winner"] = group[0]
            else:
                self._steps = []
                self.phase = Phase.CALCULATING
                self._start_tiebreak("final_first", group)
                return
        if "loser" not in f:
            group = [pid for pid in leaders(scores, highest=False) if pid != f["winner"]]
            if len(group) == 1:
                f["loser"] = group[0]
            elif not group:
                f["loser"] = None
            else:
                self._steps = []
                self.phase = Phase.CALCULATING
                self._start_tiebreak("final_last", group)
                return
        winner, loser = f["winner"], f["loser"]
        order = sorted(scores, key=lambda pid: (-scores[pid], pid != winner, pid == loser))
        assert self.last_result is not None
        self.last_result["final"] = {
            "winner_id": winner,
            "loser_id": loser,
            "ranking": [{"player_id": pid, "score": scores[pid], "place": i + 1} for i, pid in enumerate(order)],
            "tiebreak_first": f.get("steps_final_first", []),
            "tiebreak_last": f.get("steps_final_last", []),
        }
        self.phase = Phase.FINISHED

    # --------------------------------------------------------------- snapshot

    def snapshot(self, viewer_id: str) -> dict[str, Any]:
        """Hands are public; a player's plan and current pair are private until revealed."""
        def player_dict(p: _Player) -> dict:
            mine = p.player_id == viewer_id
            return {
                "player_id": p.player_id,
                "display_name": p.display_name,
                "score": p.score,
                "character": p.character.to_dict(),
                "locations": [l.to_dict() for l in p.locations],
                "existences": [e.to_dict() for e in p.existences],
                "won": [l.to_dict() for l in p.won],
                "planned": p.plan is not None,
                "away": p.away,
                "plan": ([{"location_uid": l, "existence_uid": e} for l, e in p.plan]
                         if mine and p.plan else None),
            }

        calc = None
        c = self._calc
        if c is not None:
            me = self._player(viewer_id)
            mine_pair = None
            if c.purpose == "round" and me is not None and viewer_id in c.participants:
                loc, ex = self._pair(me, self.round)
                mine_pair = {"location": loc.to_dict(), "existence": ex.to_dict()}
            calc = {
                "purpose": c.purpose,
                "tiebreak": c.is_tiebreak,
                "drinks": list(c.drinks),
                "draw_id": c.draw_id,
                "participants": list(c.participants),
                "entries": {pid: {"value": e.value, "tag": e.tag} for pid, e in c.entries.items()},
                "entered": viewer_id in c.entries,
                "mine": mine_pair,
            }
        return {
            "mode": "vydrz_rucne",
            "phase": self.phase.value,
            "round": self.round,
            "rounds_total": self.rounds_total,
            "you": viewer_id,
            "players": [player_dict(p) for p in self.players],
            "decks": {
                "chlast": len(self._drink_deck),
                "chlast_discard": len(self._drink_discard),
                "lokace": self._pool["lokace"],
                "existence": self._pool["existence"],
                "postava": len(self.content.characters) - len(self.players),
            },
            "calc": calc,
            "last_result": self.last_result,
        }
