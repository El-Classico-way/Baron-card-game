"""
server/game/game.py

Game — the authoritative game engine orchestrator.

RESPONSIBILITIES:
  - Owns and mutates GameState (the only writer).
  - Validates all incoming player actions before applying them.
  - Drives turn flow: start → draw → play → discard → end → next player.
  - Resolves card effects via the EffectResolver dispatch table.
  - Checks win conditions after every state-mutating action.
  - Emits typed events to the EventBus after every significant transition.

WHAT GAME DOES NOT DO:
  - No networking, no websockets, no HTTP.
  - No rendering or frontend concerns.
  - No randomness outside the seeded Deck RNG.

EXTENSION POINTS FOR GAME MODES:
  - Subclass Game and override:
      _assign_roles()         — different role sets
      _initial_hand_size()    — cards dealt at start
      _check_win_condition()  — custom victory logic
      _on_player_eliminated() — bounty / penalty rules
  - Or inject a GameMode strategy object (TODO: GameMode protocol).

EFFECT RESOLUTION:
  Cards carry a list of CardEffect descriptors (see card.py).  Game._resolve_effects()
  dispatches each descriptor to a handler in self._effect_handlers.  To add a new
  card behaviour, register a new handler — no changes to existing code required.

REACTION / INTERRUPT SYSTEM (FUTURE):
  When a DEAL_DAMAGE effect is about to resolve, the engine will:
    1. Emit ReactionRequestedEvent to affected players.
    2. Pause resolution (asyncio.Future or callback).
    3. Resume after timeout or when all reactions are collected.
  The TurnState already has a TODO slot for pending_reaction.  The Game class
  has a _pending_reaction hook below to make this seam explicit.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Any

if TYPE_CHECKING:
    from effects.base import EffectRegistry

from game.card import Card, CardEffect
from game.enums import (
    CardType, EffectType, EventType,
    GamePhase, PlayerStatus, Role, TurnPhase,
)
from game.events import (
    EventBus,
    CardDiscardedEvent, CardDrawnEvent, CardPlayedEvent, CardsDealtEvent,
    DamageDealtEvent, GameEndedEvent, GameStartedEvent,
    PhaseChangedEvent, PlayerEliminatedEvent, PlayerHealedEvent,
    ReactionRequestedEvent, StateSnapshotEvent,
    TurnEndedEvent, TurnStartedEvent,
)
from game.player import Player
from game.state import GameState, TurnState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action result  (returned to the caller — networking layer reads this)
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """
    Returned by every public action method on Game.

    WHY A RESULT OBJECT INSTEAD OF EXCEPTIONS:
      The networking layer needs to distinguish "illegal move" (send error to
      client) from "engine crash" (log and disconnect).  A result object makes
      the happy path and validation failures both explicit without try/except
      on the call site.

      Exceptions are still raised for genuine programming errors (KeyError on
      missing player, etc.) — those are bugs, not expected game conditions.
    """
    ok: bool
    error: str = ""                          # human-readable if ok=False
    events: list[str] = field(default_factory=list)   # EventType values emitted

    @classmethod
    def success(cls, *event_types: EventType) -> "ActionResult":
        return cls(ok=True, events=[e.value for e in event_types])

    @classmethod
    def fail(cls, reason: str) -> "ActionResult":
        return cls(ok=False, error=reason)


# ---------------------------------------------------------------------------
# EffectContext — data bundle passed into each effect handler
# ---------------------------------------------------------------------------

@dataclass
class EffectContext:
    """
    Everything an effect handler needs to do its work.

    Passing a context object instead of many positional arguments means adding
    a new field here doesn't break existing handler signatures.
    """
    game: "Game"
    state: GameState
    acting_player: Player
    target_player: Player | None
    card: Card
    effect: CardEffect


# Type alias for effect handler functions.
EffectHandler = Callable[[EffectContext], None]


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------

class Game:
    """
    The authoritative game engine for one session.

    Instantiate with an already-constructed GameState (use GameState.new()).
    Call start() to begin play.  Then route player action messages to the
    appropriate action methods (action_play_card, action_end_turn, etc.).

    The EventBus is the output channel — subscribe before calling start().

    Usage::

        state = GameState.new(player_ids=["p1","p2","p3","p4"], seed=42)
        bus   = EventBus()
        game  = Game(state=state, bus=bus)

        bus.subscribe(EventType.DAMAGE_DEALT, lambda e: print(e))

        game.start(cards)           # list[Card] to load into the deck
        game.action_end_turn("p1") # sheriff ends their turn
    """

    def __init__(
        self,
        state: GameState,
        bus: EventBus,
        registry: "EffectRegistry | None" = None,
    ) -> None:
        self.state = state
        self.bus   = bus

        # --- Effect registry (new system) ---
        # If a registry is supplied, _resolve_effects() delegates to it.
        # Effects are async-capable Effect objects (see effects/).
        #
        # If None, the legacy inline handler dict below is used instead.
        # This lets existing tests and call-sites work unchanged while the
        # effect system is adopted incrementally.
        #
        # MIGRATION PATH:
        #   Old:  Game(state, bus)
        #   New:  Game(state, bus, registry=build_default_registry())
        #
        # Once all callers pass a registry, the inline handlers and the
        # legacy fallback branch in _resolve_effects() can be deleted.
        self._registry: "EffectRegistry | None" = registry

        # --- Legacy inline handler dict (kept during migration) ---
        # When _registry is set, this dict is unused.
        # New effect types should be added to effects/ not here.
        self._effect_handlers: dict[EffectType, EffectHandler] = {
            EffectType.DEAL_DAMAGE:      self._effect_deal_damage,
            EffectType.DEAL_DAMAGE_ALL:  self._effect_deal_damage_all,
            EffectType.HEAL:             self._effect_heal,
            EffectType.DRAW_CARDS:       self._effect_draw_cards,
            EffectType.DISCARD_CARD:     self._effect_discard_card,
            EffectType.STEAL_CARD:       self._effect_steal_card,
            EffectType.FORCE_DISCARD:    self._effect_force_discard,
            EffectType.DRAW_FROM_PLAYER: self._effect_draw_from_player,
            EffectType.NEGATE_DAMAGE:    self._effect_negate_damage,
            EffectType.SET_RANGE_BONUS:  self._effect_noop,
            EffectType.SET_ATTACK_RANGE: self._effect_noop,
            EffectType.SKIP_DRAW_PHASE:  self._effect_noop,
            EffectType.CANCEL_CARD:      self._effect_noop,
            EffectType.EXTRA_CARD_PLAY:  self._effect_noop,
            EffectType.CHANGE_TURN_ORDER:self._effect_noop,
        }

        # TODO: _pending_reaction: asyncio.Future | None = None
        #       Slot reserved for the interrupt/reaction system.

    # ------------------------------------------------------------------
    # Game lifecycle
    # ------------------------------------------------------------------

    def start(self, cards: list[Card]) -> ActionResult:
        """
        Transition from LOBBY → IN_PROGRESS.

        Steps:
          1. Validate enough players.
          2. Assign roles.
          3. Load, shuffle, deal initial hands.
          4. Emit GameStartedEvent + CardsDealtEvent.
          5. Start the first turn (Sheriff goes first).
        """
        s = self.state

        if s.phase != GamePhase.LOBBY:
            return ActionResult.fail("Game has already started.")

        player_count = len(s.players)
        min_players, max_players = self._player_count_limits()
        if not (min_players <= player_count <= max_players):
            return ActionResult.fail(
                f"Need {min_players}–{max_players} players, got {player_count}."
            )

        s.phase = GamePhase.STARTING
        self._assign_roles()
        self._set_player_hp_by_role()

        s.deck.load(cards)
        s.deck.shuffle()

        hand_counts = self._deal_initial_hands()

        s.phase = GamePhase.IN_PROGRESS
        for p in s.players:
            p.status = PlayerStatus.ALIVE

        self.bus.emit(GameStartedEvent(
            player_ids=[p.player_id for p in s.players],
            mode=s.mode,
        ))
        self.bus.emit(CardsDealtEvent(player_card_counts=hand_counts))

        self._start_next_turn(first=True)
        return ActionResult.success(EventType.GAME_STARTED, EventType.CARDS_DEALT)

    def _player_count_limits(self) -> tuple[int, int]:
        """Override in subclasses to support different player counts."""
        return 2, 7   # Bang! supports 2–7

    # ------------------------------------------------------------------
    # Role assignment  (override for custom role sets)
    # ------------------------------------------------------------------

    def _assign_roles(self) -> None:
        """
        Assign roles based on player count using the standard Bang! table.

        WHY THE TABLE IS HERE AND NOT IN ENUMS:
          Role distribution is game-mode logic, not a data constant.
          High Noon mode, for example, uses a different table.
        """
        n = len(self.state.players)
        # Standard Bang! role distribution
        role_table: dict[int, list[Role]] = {
            2: [Role.SHERIFF, Role.RENEGADE],
            3: [Role.SHERIFF, Role.OUTLAW, Role.RENEGADE],
            4: [Role.SHERIFF, Role.OUTLAW, Role.OUTLAW, Role.RENEGADE],
            5: [Role.SHERIFF, Role.DEPUTY, Role.OUTLAW, Role.OUTLAW, Role.RENEGADE],
            6: [Role.SHERIFF, Role.DEPUTY, Role.OUTLAW, Role.OUTLAW, Role.OUTLAW, Role.RENEGADE],
            7: [Role.SHERIFF, Role.DEPUTY, Role.DEPUTY, Role.OUTLAW, Role.OUTLAW, Role.OUTLAW, Role.RENEGADE],
        }
        roles = role_table.get(n, [])
        if not roles:
            raise ValueError(f"No role table entry for {n} players.")

        # Shuffle roles using the game's seeded RNG for reproducibility.
        rng = random.Random(self.state.seed)
        rng.shuffle(roles)

        for player, role in zip(self.state.players, roles):
            player.role = role

        logger.info("Roles assigned for %d players.", n)

    def _set_player_hp_by_role(self) -> None:
        """
        Apply HP values by role.  Sheriff gets an extra HP.
        Override for game modes with different HP rules.
        """
        for p in self.state.players:
            if p.role == Role.SHERIFF:
                p.max_hp = 5   # Sheriff always gets +1
            else:
                p.max_hp = 4
            p.hp = p.max_hp

    # ------------------------------------------------------------------
    # Initial hand dealing
    # ------------------------------------------------------------------

    def _initial_hand_size(self, player: Player) -> int:
        """
        Cards dealt to each player at game start.
        Standard Bang!: each player starts with cards equal to their HP.
        Override for game modes with different starting hand rules.
        """
        return player.max_hp

    def _deal_initial_hands(self) -> dict[str, int]:
        """Deal opening hands to all players. Returns per-player card counts."""
        counts: dict[str, int] = {}
        for player in self.state.players:
            count = self._initial_hand_size(player)
            cards = self.state.deck.draw_many(count)
            player.deal_many(cards)
            counts[player.player_id] = len(cards)
            logger.debug(
                "Dealt %d cards to %s.", len(cards), player.player_id
            )
        return counts

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    def _start_next_turn(self, first: bool = False) -> None:
        """
        Advance to the next player's turn.

        On the very first turn, the Sheriff goes first.
        Subsequently, we advance clockwise through alive players.
        """
        s = self.state

        if first:
            sheriff = s.get_sheriff()
            if sheriff is None:
                raise RuntimeError("No sheriff assigned — role assignment failed.")
            next_player = sheriff
            s.round_number = 1
        else:
            current_id = s.turn.player_id if s.turn else s.players[0].player_id
            next_player = s.next_alive_player(current_id)
            if next_player is None:
                # Shouldn't happen — win condition should have fired first.
                logger.error("No alive players remain; ending game.")
                self._end_game(reason="no_alive_players")
                return

            # Increment round when we wrap back to the sheriff.
            if next_player.role == Role.SHERIFF:
                s.round_number += 1
                logger.info("Round %d started.", s.round_number)

        s.turn_number += 1
        s.turn = TurnState(
            player_id=next_player.player_id,
            turn_number=s.turn_number,
            phase=TurnPhase.DRAW,
        )
        next_player.reset_turn_state()

        self.bus.emit(TurnStartedEvent(
            player_id=next_player.player_id,
            turn_number=s.turn_number,
        ))
        logger.info(
            "Turn %d started for player %s.", s.turn_number, next_player.player_id
        )

    def _advance_phase(self) -> None:
        """Move the current turn to its next phase and emit PhaseChangedEvent."""
        s = self.state
        if s.turn is None:
            return
        s.turn.advance_phase()
        self.bus.emit(PhaseChangedEvent(
            player_id=s.turn.player_id,
            new_phase=s.turn.phase.value,
        ))

    # ------------------------------------------------------------------
    # Public actions — called by the networking layer
    # ------------------------------------------------------------------

    def action_draw_cards(self, player_id: str, count: int = 2) -> ActionResult:
        """
        Draw phase action: player draws their allotted cards.

        Standard Bang!: draw 2 cards from the deck.
        Some game modes allow drawing from discard (Wells Fargo, etc.) — TODO.
        """
        result = self._validate_turn_action(player_id, TurnPhase.DRAW)
        if not result.ok:
            return result

        s = self.state
        player = s.get_player(player_id)
        drawn = s.deck.draw_many(count)

        if not drawn:
            return ActionResult.fail("Deck is empty and cannot be reshuffled.")

        player.deal_many(drawn)
        s.turn.cards_drawn += len(drawn)
        player.cards_drawn_this_turn += len(drawn)

        self.bus.emit(CardDrawnEvent(
            player_id=player_id,
            count=len(drawn),
            source="deck",
        ))

        # Auto-advance to PLAY phase after drawing.
        self._advance_phase()
        return ActionResult.success(EventType.CARD_DRAWN, EventType.PHASE_CHANGED)

    def action_play_card(
        self,
        player_id: str,
        card_uid: str,
        target_id: str | None = None,
    ) -> ActionResult:
        """
        Play phase action: player plays a card from their hand.

        Validates:
          - It is this player's turn and the PLAY phase.
          - The card is in the player's hand.
          - For BROWN cards: target is valid (exists, alive, in range).
          - For BLUE cards: no duplicate equipment.
          - Bang! once-per-turn limit.
        Then removes the card from hand, resolves effects (or equips), and
        emits CardPlayedEvent.
        """
        result = self._validate_turn_action(player_id, TurnPhase.PLAY)
        if not result.ok:
            return result

        s = self.state
        player = s.get_player(player_id)

        if not player.has_card_in_hand(card_uid):
            return ActionResult.fail(f"Card {card_uid!r} is not in your hand.")

        card = next(c for c in player.hand if c.uid == card_uid)

        # --- card-type-specific validation ---
        if card.card_type == CardType.BLUE:
            val = self._validate_equip(player, card)
        else:
            val = self._validate_brown_play(player, card, target_id, s)

        if not val.ok:
            return val

        # Only now is the play legal, so only now does it use up the allowance.
        if card.id == "bang":
            player.has_played_bang = True

        # --- remove from hand ---
        player.play_card(card_uid)

        # --- resolve or equip ---
        target = s.get_player(target_id) if target_id else None

        if card.card_type == CardType.BLUE:
            player.equip(card)
            # Apply on_equip passive effects (range bonuses etc.)
            # These are passive — they are read by range_between(), not fired here.
        else:
            self._resolve_effects(card, player, target)
            s.deck.discard(card)

        self.bus.emit(CardPlayedEvent(
            player_id=player_id,
            card_id=card.id,
            card_name=card.name,
            target_id=target_id,
        ))

        # Win check after every action.
        self._check_win_condition()

        return ActionResult.success(EventType.CARD_PLAYED)

    def action_discard_card(self, player_id: str, card_uid: str) -> ActionResult:
        """
        Discard phase action: player discards a card to meet the hand limit.

        Hand limit in standard Bang! = current HP.  The engine enforces the
        limit by requiring the player to discard until hand_count ≤ hp.
        """
        result = self._validate_turn_action(player_id, TurnPhase.DISCARD)
        if not result.ok:
            return result

        s = self.state
        player = s.get_player(player_id)

        if not player.has_card_in_hand(card_uid):
            return ActionResult.fail(f"Card {card_uid!r} is not in your hand.")

        card = player.discard_card(card_uid)
        s.deck.discard(card)

        self.bus.emit(CardDiscardedEvent(
            player_id=player_id,
            card_id=card.id,
            card_name=card.name,
        ))

        return ActionResult.success(EventType.CARD_DISCARDED)

    def action_end_turn(self, player_id: str) -> ActionResult:
        """
        End the current player's turn.

        Can be called from PLAY (skipping discard if hand ≤ hp) or from
        DISCARD (once the player has discarded enough cards).
        Advances to the next player's turn.
        """
        s = self.state
        if s.turn is None or s.turn.player_id != player_id:
            return ActionResult.fail("It is not your turn.")
        if s.phase != GamePhase.IN_PROGRESS:
            return ActionResult.fail("Game is not in progress.")

        player = s.get_player(player_id)

        # Enforce discard-down if hand is over limit.
        hand_limit = player.hp   # standard rule: hand limit = current HP
        if player.hand_count() > hand_limit:
            excess = player.hand_count() - hand_limit
            if s.turn.phase == TurnPhase.PLAY:
                # Hand to be trimmed: enter the DISCARD phase so that
                # action_discard_card becomes legal.  The player then
                # discards and calls end_turn again.
                self._advance_phase()
                return ActionResult.success(EventType.PHASE_CHANGED)
            return ActionResult.fail(
                f"You must discard {excess} more card(s) before ending your turn."
            )

        self.bus.emit(TurnEndedEvent(player_id=player_id))
        self._start_next_turn()
        return ActionResult.success(EventType.TURN_ENDED, EventType.TURN_STARTED)

    def action_request_snapshot(self, player_id: str) -> ActionResult:
        """
        Emit a full state snapshot (used on reconnect or initial sync).
        The networking layer attaches each player's private hand before forwarding.
        """
        self.bus.emit(StateSnapshotEvent(state=self.state.to_public_dict()))
        return ActionResult.success(EventType.STATE_SNAPSHOT)

    # ------------------------------------------------------------------
    # Action validation helpers
    # ------------------------------------------------------------------

    def _validate_turn_action(
        self, player_id: str, required_phase: TurnPhase
    ) -> ActionResult:
        """Common guard: correct player, correct phase, game in progress."""
        s = self.state
        if s.phase != GamePhase.IN_PROGRESS:
            return ActionResult.fail("Game is not in progress.")
        if s.turn is None:
            return ActionResult.fail("No active turn.")
        if s.turn.player_id != player_id:
            return ActionResult.fail("It is not your turn.")
        if s.turn.phase != required_phase:
            return ActionResult.fail(
                f"Wrong phase: expected {required_phase.value}, "
                f"currently {s.turn.phase.value}."
            )
        return ActionResult.success()

    def _validate_brown_play(
        self,
        player: Player,
        card: Card,
        target_id: str | None,
        s: GameState,
    ) -> ActionResult:
        """
        Validate playing a BROWN (single-use) card.

        Checks:
          - Bang! once-per-turn rule.
          - Target exists and is alive (for targeted cards).
          - Target is within range (for attack cards).
        """
        # Bang! once-per-turn limit (unless a Volcanic is equipped).
        if card.id == "bang":
            has_volcanic = player.has_equipment("volcanic")
            if player.has_played_bang and not has_volcanic:
                return ActionResult.fail("You can only play one Bang! per turn.")

        # Cards that require a target.
        needs_target = any(
            e.type in (
                EffectType.DEAL_DAMAGE,
                EffectType.STEAL_CARD,
                EffectType.DISCARD_CARD,
                EffectType.FORCE_DISCARD,
                EffectType.DRAW_FROM_PLAYER,
            )
            for e in card.effects
        )

        if needs_target:
            if not target_id:
                return ActionResult.fail(f"Card '{card.name}' requires a target.")
            if target_id == player.player_id:
                return ActionResult.fail("You cannot target yourself.")
            try:
                target = s.get_player(target_id)
            except KeyError:
                return ActionResult.fail(f"Target player {target_id!r} not found.")
            if not target.is_alive:
                return ActionResult.fail("Target player is already eliminated.")

            # Range check for attack cards.
            if card.max_range is not None:
                effective = s.range_between(player.player_id, target_id)
                if effective > card.max_range:
                    return ActionResult.fail(
                        f"{target.display_name} is out of range "
                        f"(range {effective}, card max {card.max_range})."
                    )

        return ActionResult.success()

    def _validate_equip(self, player: Player, card: Card) -> ActionResult:
        """Validate playing a BLUE (equipment) card."""
        if player.has_equipment(card.definition.id):
            return ActionResult.fail(
                f"You already have '{card.name}' equipped."
            )
        return ActionResult.success()

    # ------------------------------------------------------------------
    # Effect resolution
    # ------------------------------------------------------------------

    def _resolve_effects(
        self,
        card: Card,
        actor: Player,
        target: Player | None,
    ) -> None:
        """
        Dispatch each CardEffect on the card to its registered handler.

        DUAL-PATH DISPATCH:
          If self._registry is set (new system): delegates to the EffectRegistry
          which holds async Effect objects.  The async resolve() is driven via
          _run_async_effect(), a sync-safe bridge using asyncio.

          If self._registry is None (legacy): falls back to the inline handler
          dict of sync functions, preserving full backward compatibility.

        WHY ORDERED EFFECTS:
          Order matters — effects are processed in the sequence defined in the
          card's JSON definition.  e.g. a card that deals damage then draws
          cards must deal damage first so elimination fires before the draw.

        WHY NOT async _resolve_effects():
          Game's public action methods (action_play_card etc.) are sync — they
          are called by the networking layer which may or may not be in an async
          context.  Making them async is the right long-term answer but requires
          coordinating with the networking layer.  The bridge keeps the engine
          sync at its public surface while effects themselves are async inside.
          Once the networking layer is fully async, remove the bridge and make
          this method async directly.
        """
        for effect in card.effects:
            ctx = EffectContext(
                game=self,
                state=self.state,
                acting_player=actor,
                target_player=target,
                card=card,
                effect=effect,
            )

            if self._registry is not None:
                # New path: async Effect objects via sync bridge.
                self._run_async_effect(ctx)
            else:
                # Legacy path: sync handler functions.
                handler = self._effect_handlers.get(effect.type)
                if handler is None:
                    logger.warning(
                        "No handler registered for effect %s on card %s.",
                        effect.type, card.id,
                    )
                    continue
                handler(ctx)

    def _run_async_effect(self, ctx: EffectContext) -> None:
        """
        Sync-safe bridge: runs one async Effect.resolve() to completion.

        STRATEGY — try running inside an existing event loop first:
          In a fully async server (FastAPI + asyncio), there will already be
          a running loop.  We schedule the coroutine as a task and block via
          asyncio.get_event_loop().run_until_complete() — but that raises
          RuntimeError if called from inside a running loop.

          So we use the standard pattern:
            1. If no running loop  → asyncio.run()  (test / CLI context)
            2. If running loop     → loop.run_until_complete() would deadlock,
                                     so we use a new thread's event loop via
                                     asyncio.run_coroutine_threadsafe().

          LONG-TERM: make Game._resolve_effects() and action_play_card() async,
          remove this bridge entirely, and await the registry directly.

        TODO: Replace with `await registry.resolve(ctx)` once the public
              action methods are async.
        """
        coro = self._registry.resolve(ctx)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            # No running loop — safe to use asyncio.run() (tests, CLI).
            asyncio.run(coro)
        else:
            # Running inside an async context (FastAPI, pytest-asyncio).
            # Schedule as a fire-and-forget task; the effect runs before the
            # next await point in the caller.
            # NOTE: This means elimination and win-condition checks that happen
            # *inside* the effect (e.g. DamageEffect._eliminate_player) are
            # deferred to the next event loop iteration.  This is acceptable
            # now but must be resolved when the full async migration is done.
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                future.result(timeout=5.0)   # 5s hard limit; no effect should block
            except concurrent.futures.TimeoutError:
                logger.error(
                    "Effect %s on card %s timed out after 5s.",
                    ctx.effect.type, ctx.card.id,
                )

    # ------------------------------------------------------------------
    # Effect handlers
    # ------------------------------------------------------------------

    def _effect_deal_damage(self, ctx: EffectContext) -> None:
        """
        Deal damage to the target player.

        FUTURE — REACTION HOOK:
          Before applying damage, the engine should emit ReactionRequestedEvent
          and wait for the target to play a 'Missed!' or similar reaction card.
          The TODO below marks exactly where that pause needs to go.
        """
        if ctx.target_player is None:
            logger.warning("DEAL_DAMAGE effect fired with no target.")
            return

        amount = ctx.effect.params.get("amount", 1)

        # TODO: emit ReactionRequestedEvent here and await reaction before
        #       applying damage.  Requires the async reaction system.
        #       Hook: result = self._request_reaction(ctx, EffectType.NEGATE_DAMAGE)

        new_hp = ctx.target_player.take_damage(amount)

        self.bus.emit(DamageDealtEvent(
            source_id=ctx.acting_player.player_id,
            target_id=ctx.target_player.player_id,
            amount=amount,
            cause=ctx.card.name,
        ))

        if new_hp <= 0:
            self._eliminate_player(
                ctx.target_player,
                eliminated_by=ctx.acting_player.player_id,
            )

    def _effect_deal_damage_all(self, ctx: EffectContext) -> None:
        """
        Deal damage to all OTHER players (e.g. Gatling).

        TODO: Each damaged player should have an opportunity to react
              with 'Missed!' before the damage resolves.
        """
        amount = ctx.effect.params.get("amount", 1)
        others = [
            p for p in self.state.alive_players()
            if p.player_id != ctx.acting_player.player_id
        ]
        for target in others:
            new_hp = target.take_damage(amount)
            self.bus.emit(DamageDealtEvent(
                source_id=ctx.acting_player.player_id,
                target_id=target.player_id,
                amount=amount,
                cause=ctx.card.name,
            ))
            if new_hp <= 0:
                self._eliminate_player(
                    target,
                    eliminated_by=ctx.acting_player.player_id,
                )

    def _effect_heal(self, ctx: EffectContext) -> None:
        """Restore HP to the acting player (e.g. Beer). Max = max_hp."""
        amount = ctx.effect.params.get("amount", 1)
        healed = ctx.acting_player.heal(amount)
        self.bus.emit(PlayerHealedEvent(
            player_id=ctx.acting_player.player_id,
            amount=healed,
            new_hp=ctx.acting_player.hp,
        ))

    def _effect_draw_cards(self, ctx: EffectContext) -> None:
        """Draw cards from the deck into the acting player's hand (e.g. Stagecoach)."""
        count = ctx.effect.params.get("count", 1)
        drawn = self.state.deck.draw_many(count)
        ctx.acting_player.deal_many(drawn)
        self.bus.emit(CardDrawnEvent(
            player_id=ctx.acting_player.player_id,
            count=len(drawn),
            source="deck",
        ))

    def _effect_discard_card(self, ctx: EffectContext) -> None:
        """
        Force target to discard a random card from hand (e.g. Cat Balou — vs equipment).

        TODO: Cat Balou targets either a hand card or an equipment card.
              The target selection logic should come from the action payload.
              For now: discard a random hand card.
        """
        if ctx.target_player is None or ctx.target_player.hand_count() == 0:
            return
        rng = random.Random(self.state.seed)
        victim_card = rng.choice(ctx.target_player.hand)
        ctx.target_player.lose_card_from_hand(victim_card.uid)
        self.state.deck.discard(victim_card)
        self.bus.emit(CardDiscardedEvent(
            player_id=ctx.target_player.player_id,
            card_id=victim_card.id,
            card_name=victim_card.name,
        ))

    def _effect_steal_card(self, ctx: EffectContext) -> None:
        """
        Steal a random card from target's hand (e.g. Panic!).

        TODO: player should choose which card to steal (from hand or equipment).
        """
        if ctx.target_player is None or ctx.target_player.hand_count() == 0:
            return
        rng = random.Random(self.state.seed)
        stolen = rng.choice(ctx.target_player.hand)
        ctx.target_player.lose_card_from_hand(stolen.uid)
        ctx.acting_player.deal(stolen)
        self.bus.emit(CardDrawnEvent(
            player_id=ctx.acting_player.player_id,
            count=1,
            source=f"player:{ctx.target_player.player_id}",
        ))

    def _effect_force_discard(self, ctx: EffectContext) -> None:
        """Alias for discard_card — both semantics are supported in the data."""
        self._effect_discard_card(ctx)

    def _effect_draw_from_player(self, ctx: EffectContext) -> None:
        """Alias for steal_card — draw from player's hand into own hand."""
        self._effect_steal_card(ctx)

    def _effect_negate_damage(self, ctx: EffectContext) -> None:
        """
        Cancel incoming damage (e.g. Missed!).

        This effect is played as a REACTION, not as a main action.
        The reaction system is not yet implemented — this handler is a stub
        so the effect type is registered and the card can be loaded.
        TODO: wire into the reaction/interrupt system.
        """
        logger.debug(
            "NEGATE_DAMAGE effect invoked — reaction system not yet implemented."
        )

    def _effect_noop(self, ctx: EffectContext) -> None:
        """
        Effects that are passive (applied via equipment) or not yet implemented.
        Registered to prevent 'no handler' warnings during early development.
        """
        pass

    # ------------------------------------------------------------------
    # Player elimination
    # ------------------------------------------------------------------

    def _eliminate_player(
        self, player: Player, eliminated_by: str | None = None
    ) -> None:
        """
        Mark a player as dead, reveal their role, discard their hand and equipment,
        apply any elimination side-effects, then check win conditions.

        WHY ROLE REVEAL ON DEATH:
          Bang! rule — players reveal their role card when eliminated.
          The networking layer reads role from to_public_dict() which exposes
          it only when is_dead is True.
        """
        player.eliminate(eliminated_by)

        # Discard hand
        hand_copy = list(player.hand)
        for card in hand_copy:
            player.lose_card_from_hand(card.uid)
            self.state.deck.discard(card)

        # Discard equipment
        equip_copy = list(player.equipment.keys())
        for uid in equip_copy:
            card = player.unequip(uid)
            self.state.deck.discard(card)

        self.bus.emit(PlayerEliminatedEvent(
            player_id=player.player_id,
            eliminated_by=eliminated_by,
            role=player.role.value if player.role else "",
        ))

        self._on_player_eliminated(player, eliminated_by)
        self._check_win_condition()

    def _on_player_eliminated(
        self, player: Player, eliminated_by: str | None
    ) -> None:
        """
        Hook for elimination side-effects.

        Standard Bang! rules:
          - Sheriff kills a Deputy → Sheriff discards ALL hand + equipment.
          - Outlaw killed → killer draws 3 cards as bounty.
        Override in game-mode subclasses as needed.
        """
        eliminator = (
            self.state.get_player(eliminated_by) if eliminated_by else None
        )

        # Outlaw bounty: whoever kills an outlaw draws 3 cards.
        if player.role == Role.OUTLAW and eliminator is not None:
            bounty = self.state.deck.draw_many(3)
            eliminator.deal_many(bounty)
            self.bus.emit(CardDrawnEvent(
                player_id=eliminator.player_id,
                count=len(bounty),
                source="bounty",
            ))
            logger.info(
                "%s earned a 3-card bounty for eliminating outlaw %s.",
                eliminator.player_id, player.player_id,
            )

        # Sheriff penalty: kills their own deputy → discard everything.
        if (
            player.role == Role.DEPUTY
            and eliminator is not None
            and eliminator.role == Role.SHERIFF
        ):
            hand_copy = list(eliminator.hand)
            for card in hand_copy:
                eliminator.lose_card_from_hand(card.uid)
                self.state.deck.discard(card)
            equip_copy = list(eliminator.equipment.keys())
            for uid in equip_copy:
                card = eliminator.unequip(uid)
                self.state.deck.discard(card)
            logger.info(
                "Sheriff %s penalised for killing Deputy %s — hand discarded.",
                eliminator.player_id, player.player_id,
            )

    # ------------------------------------------------------------------
    # Win condition  (override for custom victory rules)
    # ------------------------------------------------------------------

    def _check_win_condition(self) -> None:
        """
        Evaluate win conditions after every state change.

        Standard Bang! win conditions:
          OUTLAWS win:   Sheriff is dead.
          SHERIFF wins:  All outlaws and renegade are dead.
          RENEGADE wins: Is the last player standing when all outlaws/deputies dead
                         (handled by sheriff-wins check — renegade must then beat sheriff).

        Override this method entirely in game-mode subclasses.
        """
        s = self.state
        if s.phase != GamePhase.IN_PROGRESS:
            return

        sheriff = s.get_sheriff()

        # Outlaws win if the Sheriff is eliminated.
        if sheriff is None or sheriff.is_dead:
            outlaws = s.players_with_role(Role.OUTLAW)
            renegades = s.players_with_role(Role.RENEGADE)
            winners = [p.player_id for p in outlaws + renegades]
            self._end_game(winner_ids=winners, reason="sheriff_eliminated")
            return

        # Sheriff wins if all outlaws and renegades are dead.
        alive_hostiles = (
            s.alive_players_with_role(Role.OUTLAW)
            + s.alive_players_with_role(Role.RENEGADE)
        )
        if not alive_hostiles:
            winners = [
                p.player_id
                for p in s.alive_players_with_role(Role.SHERIFF)
                + s.alive_players_with_role(Role.DEPUTY)
            ]
            self._end_game(winner_ids=winners, reason="all_hostiles_eliminated")

    def _end_game(
        self,
        winner_ids: list[str] | None = None,
        reason: str = "",
    ) -> None:
        """Transition to FINISHED and emit GameEndedEvent."""
        s = self.state
        s.phase = GamePhase.FINISHED
        s.winner_ids = winner_ids or []
        self.bus.emit(GameEndedEvent(
            winning_team=s.winner_ids,
            reason=reason,
        ))
        logger.info(
            "Game %s ended. Winners: %s. Reason: %s.",
            s.game_id[:8], s.winner_ids, reason,
        )
