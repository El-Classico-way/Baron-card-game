/**
 * client/js/core/store.js
 *
 * Client-side state store — the single source of truth for all UI state.
 *
 * DESIGN PRINCIPLES
 * ─────────────────
 * 1. SERVER IS AUTHORITATIVE.
 *    The store never speculatively mutates game state.  It waits for the
 *    server to confirm (via events or snapshots) before updating anything
 *    a player can see.  This is the fundamental rule that prevents desync.
 *
 * 2. SNAPSHOT IS THE RESET LEVER.
 *    On reconnect, the server sends a full state_snapshot.  The store
 *    replaces its entire state from that snapshot.  No incremental repair,
 *    no diffing — full replacement.  This makes reconnect trivially safe.
 *
 * 3. EVENTS PATCH STATE, SNAPSHOT REPLACES IT.
 *    game_event packets carry individual changes (damage, draws, etc.).
 *    The store applies each one as a minimal patch to the current state.
 *    If anything looks wrong, calling socket.sendRequestSnapshot() forces
 *    a full reset.
 *
 * 4. PRIVATE HAND IS SACRED.
 *    Only the local player's hand cards are known.  Opponents' hands are
 *    represented only as a count.  The store enforces this boundary.
 *
 * STATE SHAPE:
 *   {
 *     // Session
 *     myPlayerId:   string | null,
 *     myRole:       string | null,     // "sheriff" | "deputy" | "outlaw" | "renegade"
 *     roomId:       string | null,
 *
 *     // Lobby (before game starts)
 *     lobby: {
 *       players:       [{ player_id, display_name, connected }],
 *       hostPlayerId:  string | null,
 *       gamePhase:     string,          // "lobby" | "playing" | "finished"
 *       maxPlayers:    number,
 *     },
 *
 *     // Game (after game starts — null until state_snapshot received)
 *     game: null | {
 *       gameId:       string,
 *       phase:        string,           // "in_progress" | "finished"
 *       mode:         string,
 *       turnNumber:   number,
 *       roundNumber:  number,
 *       winnerIds:    string[],
 *       turn: null | {
 *         playerId:   string,
 *         phase:      string,           // "draw" | "play" | "discard" | "end"
 *         cardsDrawn: number,
 *       },
 *       deck: {
 *         drawCount:    number,
 *         discardCount: number,
 *         topDiscard:   null | { id, name, suit, rank },
 *       },
 *       players: [
 *         {
 *           player_id:    string,
 *           display_name: string,
 *           hp:           number,
 *           max_hp:       number,
 *           status:       string,       // "alive" | "dead"
 *           hand_count:   number,       // always present (public)
 *           equipment:    [{ uid, id, name, suit, rank }],
 *           role:         string | null,  // null until revealed
 *           // Added for local player only:
 *           hand?:        [{ uid, id, name, suit, rank, description }],
 *         }
 *       ],
 *     },
 *
 *     // UI transient state (not from server — cleared on snapshot)
 *     ui: {
 *       selectedCardUid:   string | null,
 *       pendingAction:     string | null,  // "draw" | "play_card" | "discard" | "end_turn"
 *       errorMessage:      string | null,
 *       notification:      null | { message, type, id },
 *       chat:              [{ sender_id, display_name, message, timestamp }],
 *       connectionStatus:  string,         // "connected" | "disconnected" | "reconnecting"
 *       reconnectCountdown: number | null,
 *     }
 *   }
 */

import { bus } from "./events.js";

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

function initialState() {
  return {
    myPlayerId: null,
    myRole:     null,
    roomId:     null,

    lobby: {
      players:      [],
      hostPlayerId: null,
      gamePhase:    "lobby",
      maxPlayers:   7,
    },

    game: null,

    ui: {
      selectedCardUid:    null,
      pendingAction:      null,
      errorMessage:       null,
      notification:       null,
      chat:               [],
      connectionStatus:   "disconnected",
      reconnectCountdown: null,
    },
  };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export class Store {
  constructor() {
    this._state = initialState();
    this._subscribeToServer();
    this._subscribeToSocket();
  }

  // ------------------------------------------------------------------
  // Public read access
  // ------------------------------------------------------------------

  /** Full state snapshot (read-only reference — do not mutate). */
  get state() { return this._state; }

  get myPlayer() {
    if (!this._state.game || !this._state.myPlayerId) return null;
    return this._state.game.players.find(
      p => p.player_id === this._state.myPlayerId
    ) ?? null;
  }

  get isMyTurn() {
    return this._state.game?.turn?.playerId === this._state.myPlayerId;
  }

  get currentTurnPhase() {
    return this._state.game?.turn?.phase ?? null;
  }

  // ------------------------------------------------------------------
  // Server packet handlers
  // (subscribed in _subscribeToServer — each mutates state then emits
  //  a "store:<topic>" event so the renderer knows what changed)
  // ------------------------------------------------------------------

  _subscribeToServer() {
    // Session establishment
    bus.on("server:joined",       p => this._onJoined(p));

    // Lobby management
    bus.on("server:room_state",   p => this._onRoomState(p));
    bus.on("server:player_connected",    p => this._onPlayerConnected(p));
    bus.on("server:player_disconnected", p => this._onPlayerDisconnected(p));

    // State sync (highest priority — always safe)
    bus.on("server:state_snapshot", p => this._onStateSnapshot(p));
    bus.on("server:private_hand",   p => this._onPrivateHand(p));

    // Game events (incremental patches)
    bus.on("server:game_event", p => this._onGameEvent(p));

    // Feedback to the acting player
    bus.on("server:action_ack", p => this._onActionAck(p));
    bus.on("server:error",      p => this._onError(p));

    // Chat
    bus.on("server:chat", p => this._onChat(p));
  }

  _subscribeToSocket() {
    bus.on("socket:connected",    () => this._setConnectionStatus("connected"));
    bus.on("socket:disconnected", () => this._setConnectionStatus("disconnected"));
    bus.on("socket:reconnecting", ({ delay }) => {
      this._setConnectionStatus("reconnecting");
      this._state.ui.reconnectCountdown = Math.round(delay / 1000);
      bus.emit("store:connection_changed", this._state.ui.connectionStatus);
    });
  }

  // ------------------------------------------------------------------
  // Session
  // ------------------------------------------------------------------

  _onJoined({ room_id, player_id, display_name }) {
    this._state.myPlayerId = player_id;
    this._state.roomId     = room_id;
    bus.emit("store:joined", { roomId: room_id, playerId: player_id, displayName: display_name });
  }

  // ------------------------------------------------------------------
  // Lobby
  // ------------------------------------------------------------------

  _onRoomState({ room_id, host_player_id, players, game_phase, max_players, mode, modes }) {
    this._state.lobby.mode         = mode ?? null;
    this._state.lobby.modes        = modes ?? [];
    this._state.lobby.players      = players;
    this._state.lobby.hostPlayerId = host_player_id;
    this._state.lobby.gamePhase    = game_phase;
    this._state.lobby.maxPlayers   = max_players;
    bus.emit("store:lobby_updated", this._state.lobby);
  }

  _onPlayerConnected({ player_id, display_name }) {
    const p = this._state.lobby.players.find(p => p.player_id === player_id);
    if (p) {
      p.connected = true;
    } else {
      this._state.lobby.players.push({ player_id, display_name, connected: true });
    }
    // Mark player alive in game state if game is running
    if (this._state.game) {
      const gp = this._state.game.players.find(p => p.player_id === player_id);
      if (gp) gp.connected = true;
    }
    this._notify(`${display_name} reconnected`, "info");
    bus.emit("store:lobby_updated", this._state.lobby);
  }

  _onPlayerDisconnected({ player_id, display_name }) {
    const p = this._state.lobby.players.find(p => p.player_id === player_id);
    if (p) p.connected = false;
    if (this._state.game) {
      const gp = this._state.game.players.find(p => p.player_id === player_id);
      if (gp) gp.connected = false;
    }
    this._notify(`${display_name} disconnected`, "warn");
    bus.emit("store:lobby_updated", this._state.lobby);
  }

  // ------------------------------------------------------------------
  // State sync — the safe path
  // ------------------------------------------------------------------

  _onStateSnapshot({ state, your_player }) {
    /**
     * DESYNC PREVENTION — THIS IS THE KEY METHOD.
     *
     * When any doubt exists about client state validity, the server sends
     * this packet.  We replace game state wholesale from it, then merge
     * in the private player data.
     *
     * WHY NOT DIFF:
     *   Diffing is fragile — a missed field leaves stale state.  Full
     *   replacement is O(n) on player count, which is at most 7.
     *   The simplicity is worth it.
     */
    const players = state.players.map(p => ({ ...p }));

    // Merge private data for ourselves — this is the only place where
    // hand card identities are written into client state.
    if (your_player) {
      const me = players.find(p => p.player_id === your_player.player_id);
      if (me) {
        Object.assign(me, your_player);
      }
      this._state.myRole = your_player.role;
    }

    this._state.game = {
      gameId:      state.game_id,
      phase:       state.phase,
      mode:        state.mode,
      turnNumber:  state.turn_number,
      roundNumber: state.round_number,
      winnerIds:   state.winner_ids ?? [],
      turn:        state.turn ? {
        playerId:   state.turn.player_id,
        phase:      state.turn.phase,
        cardsDrawn: state.turn.cards_drawn,
      } : null,
      deck: {
        drawCount:    state.deck.draw_count,
        discardCount: state.deck.discard_count,
        topDiscard:   state.deck.top_discard ?? null,
      },
      players,
    };

    // Clear transient UI state on resync — stale selections are dangerous.
    this._state.ui.selectedCardUid = null;
    this._state.ui.pendingAction   = null;
    this._state.ui.errorMessage    = null;

    bus.emit("store:state_replaced", this._state.game);
  }

  // ------------------------------------------------------------------
  // Private hand update (lighter than full snapshot)
  // ------------------------------------------------------------------

  _onPrivateHand({ player_id, hand, hand_count, role }) {
    /**
     * Server sends this after any event that changes a player's hand:
     *   - draw_cards → CARD_DRAWN event → private_hand to the drawing player
     *   - cards_dealt at game start → private_hand to each player
     *
     * We only accept hand data for ourselves — other players' hands are
     * never written here even if the server accidentally sent them.
     */
    if (player_id !== this._state.myPlayerId) return;
    if (!this._state.game) return;

    const player = this._state.game.players.find(p => p.player_id === player_id);
    if (!player) return;

    player.hand       = hand;
    player.hand_count = hand_count;
    if (role !== undefined) {
      player.role        = role;
      this._state.myRole = role;
    }

    bus.emit("store:hand_updated", { hand, handCount: hand_count });
  }

  // ------------------------------------------------------------------
  // Game event patches
  // ------------------------------------------------------------------

  _onGameEvent({ event_type, data }) {
    /**
     * Each game_event is a minimal patch to the current state.
     * The handler for each event type updates only the fields that changed.
     *
     * WHY NOT WAIT FOR SNAPSHOTS:
     *   Snapshots are only sent on connect/reconnect.  During play, the
     *   server sends discrete events.  We patch here, and request a
     *   snapshot if anything seems inconsistent.
     */
    const handlers = {
      game_started:       () => this._evGameStarted(data),
      game_ended:         () => this._evGameEnded(data),
      turn_started:       () => this._evTurnStarted(data),
      turn_ended:         () => this._evTurnEnded(data),
      phase_changed:      () => this._evPhaseChanged(data),
      damage_dealt:       () => this._evDamageDealt(data),
      player_healed:      () => this._evPlayerHealed(data),
      player_eliminated:  () => this._evPlayerEliminated(data),
      card_played:        () => this._evCardPlayed(data),
      card_drawn:         () => this._evCardDrawn(data),
      card_discarded:     () => this._evCardDiscarded(data),
      cards_dealt:        () => this._evCardsDealt(data),
      reaction_requested: () => this._evReactionRequested(data),
    };

    const handler = handlers[event_type];
    if (handler) {
      handler();
    } else {
      // Unknown event — log but don't crash.
      console.debug("[Store] Unhandled game_event:", event_type, data);
    }
  }

  _evGameStarted({ player_ids, mode }) {
    this._state.lobby.gamePhase = "playing";
    bus.emit("store:game_started", { playerIds: player_ids, mode });
  }

  _evGameEnded({ winning_team, reason }) {
    if (this._state.game) {
      this._state.game.phase     = "finished";
      this._state.game.winnerIds = winning_team;
    }
    bus.emit("store:game_ended", { winnerIds: winning_team, reason });
  }

  _evTurnStarted({ player_id, turn_number }) {
    if (!this._state.game) return;
    this._state.game.turn = { playerId: player_id, phase: "draw", cardsDrawn: 0 };
    this._state.game.turnNumber = turn_number;
    // Clear selection when turn changes.
    this._state.ui.selectedCardUid = null;
    this._state.ui.pendingAction   = null;
    bus.emit("store:turn_changed", { playerId: player_id, isMyTurn: this.isMyTurn });
  }

  _evTurnEnded({ player_id }) {
    bus.emit("store:turn_ended", { playerId: player_id });
  }

  _evPhaseChanged({ player_id, new_phase }) {
    if (this._state.game?.turn) {
      this._state.game.turn.phase = new_phase;
    }
    bus.emit("store:phase_changed", { playerId: player_id, phase: new_phase });
  }

  _evDamageDealt({ source_id, target_id, amount, cause }) {
    this._patchPlayer(target_id, p => { p.hp = Math.max(0, p.hp - amount); });
    bus.emit("store:damage_dealt", { sourceId: source_id, targetId: target_id, amount, cause });
  }

  _evPlayerHealed({ player_id, amount, new_hp }) {
    this._patchPlayer(player_id, p => { p.hp = new_hp; });
    bus.emit("store:player_healed", { playerId: player_id, amount, newHp: new_hp });
  }

  _evPlayerEliminated({ player_id, eliminated_by, role }) {
    this._patchPlayer(player_id, p => {
      p.status = "dead";
      p.hp     = 0;
      if (role) p.role = role;  // role revealed on elimination
    });
    bus.emit("store:player_eliminated", { playerId: player_id, eliminatedBy: eliminated_by, role });
  }

  _evCardPlayed({ player_id, card_id, card_name, target_id }) {
    // Opponent's hand count decreases by 1 when they play.
    // Our own hand is kept accurate by private_hand updates.
    if (player_id !== this._state.myPlayerId) {
      this._patchPlayer(player_id, p => {
        p.hand_count = Math.max(0, (p.hand_count ?? 1) - 1);
      });
    }
    bus.emit("store:card_played", { playerId: player_id, cardId: card_id, cardName: card_name, targetId: target_id });
  }

  _evCardDrawn({ player_id, count, source }) {
    // Our hand card identities are updated by private_hand.
    // Opponents: just increment hand_count so the count display stays correct.
    if (player_id !== this._state.myPlayerId) {
      this._patchPlayer(player_id, p => {
        p.hand_count = (p.hand_count ?? 0) + count;
      });
    }
    if (this._state.game?.deck) {
      this._state.game.deck.drawCount = Math.max(
        0, this._state.game.deck.drawCount - count
      );
    }
    bus.emit("store:card_drawn", { playerId: player_id, count, source });
  }

  _evCardDiscarded({ player_id, card_id, card_name }) {
    if (player_id !== this._state.myPlayerId) {
      this._patchPlayer(player_id, p => {
        p.hand_count = Math.max(0, (p.hand_count ?? 1) - 1);
      });
    }
    bus.emit("store:card_discarded", { playerId: player_id, cardId: card_id, cardName: card_name });
  }

  _evCardsDealt({ player_card_counts }) {
    // hand_count per player — private_hand follows for us.
    if (!this._state.game) return;
    for (const [playerId, count] of Object.entries(player_card_counts)) {
      this._patchPlayer(playerId, p => { p.hand_count = count; });
    }
    bus.emit("store:cards_dealt", { counts: player_card_counts });
  }

  _evReactionRequested({ trigger_card_id, reacting_player_ids, timeout_seconds }) {
    // Only relevant if we are one of the reacting players.
    if (reacting_player_ids.includes(this._state.myPlayerId)) {
      bus.emit("store:reaction_window_open", { triggerCardId: trigger_card_id, timeoutSeconds: timeout_seconds });
    }
  }

  // ------------------------------------------------------------------
  // Ack and error
  // ------------------------------------------------------------------

  _onActionAck({ action }) {
    this._state.ui.pendingAction = null;
    bus.emit("store:action_acked", { action });
  }

  _onError({ message, code }) {
    this._state.ui.errorMessage  = message;
    this._state.ui.pendingAction = null;
    this._notify(message, "error");
    bus.emit("store:error", { message, code });
  }

  // ------------------------------------------------------------------
  // Chat
  // ------------------------------------------------------------------

  _onChat(msg) {
    this._state.ui.chat.push(msg);
    // Keep last 100 messages.
    if (this._state.ui.chat.length > 100) {
      this._state.ui.chat.shift();
    }
    bus.emit("store:chat_received", msg);
  }

  // ------------------------------------------------------------------
  // UI state mutations (called by the UI layer, not the server)
  // ------------------------------------------------------------------

  selectCard(cardUid) {
    this._state.ui.selectedCardUid = cardUid;
    bus.emit("store:selection_changed", { cardUid });
  }

  clearSelection() {
    this._state.ui.selectedCardUid = null;
    bus.emit("store:selection_changed", { cardUid: null });
  }

  setPendingAction(action) {
    this._state.ui.pendingAction = action;
    bus.emit("store:pending_action_changed", { action });
  }

  clearError() {
    this._state.ui.errorMessage = null;
    bus.emit("store:error_cleared", {});
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  _patchPlayer(playerId, fn) {
    if (!this._state.game) return;
    const player = this._state.game.players.find(p => p.player_id === playerId);
    if (player) fn(player);
  }

  _setConnectionStatus(status) {
    this._state.ui.connectionStatus  = status;
    this._state.ui.reconnectCountdown = null;
    bus.emit("store:connection_changed", status);
  }

  _notify(message, type = "info") {
    this._state.ui.notification = { message, type, id: Date.now() };
    bus.emit("store:notification", this._state.ui.notification);
  }
}
