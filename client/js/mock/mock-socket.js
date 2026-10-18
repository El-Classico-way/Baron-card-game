/**
 * client/js/mock/mock-socket.js
 *
 * MOCK BACKEND — demo only. NOT the game server and NOT game rules.
 *
 * Drop-in replacement for SocketClient (same public methods). It plays the
 * role of the authoritative server just enough to let the UI be evaluated
 * without a backend:
 *
 *   - It emits the same "server:<type>" packets the real server sends, so
 *     Store, Renderer, Controller and Animator run completely unchanged.
 *   - After every action it sends a full state_snapshot, exactly like the
 *     real protocol's resync path. The UI never computes outcomes itself.
 *
 * What is deliberately fake:
 *   - Cards are generic placeholders (suit + rank only). They have no effects.
 *   - Playing a card just moves it to the discard pile. No damage, healing,
 *     range checks, hand limits, roles, elimination or winning.
 *   - Bots "take a turn" by drawing, discarding one card and ending the turn.
 *
 * Everything here is invented scaffolding; remove this folder (and the
 * `?mock` branch in app.js) when real rules are wired up.
 */

import { bus } from "../core/events.js";
import { MockVydrz } from "./mock-vydrz.js";
import { MockManual } from "./mock-vydrz-manual.js";

const BOT_NAMES = ["Ada", "Bruno", "Cleo", "Dax", "Esme", "Finn"];
const SUITS     = ["spades", "hearts", "diamonds", "clubs"];
const RANKS     = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"];
const ME        = "me";
const MODES     = [
  { id: "rezba", name: "Řežba", family: "rezba", variant: "", playable: false },
  { id: "vydrz", name: "Výdrž", family: "vydrz", variant: "Automaticky", playable: true },
  { id: "vydrz_rucne", name: "Výdrž", family: "vydrz", variant: "Ručně", playable: true },
];
const STARTING_HAND = 4;     // placeholder numbers, not rules
const MOCK_HP       = 4;
const DECK_SIZE     = 60;

let _uid = 0;
const _pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

function makeCard() {
  const n = ++_uid;
  return {
    uid: `mock-${n}`, id: "placeholder", name: `Karta ${n}`,
    suit: _pick(SUITS), rank: _pick(RANKS),
    // Every third card gets long text so wrapping in the inspector can be judged.
    description: n % 3 === 0
      ? "Zástupný text místo pravidel karty. Skutečný text pošle server a může mít i několik řádků, takže se v detailu karty musí pohodlně zalamovat i na malém displeji."
      : "Zástupná karta. Skutečný text karty přijde ze serveru.",
    has_art: false,
  };
}

export class MockSocket {
  /**
   * @param {{ bots?: number, autostart?: boolean }} opts
   *   bots       number of fake opponents (1-6)
   *   autostart  skip the lobby and go straight to the table
   */
  constructor({ bots = 3, autostart = false, mode = null } = {}) {
    this._botCount = Math.min(6, Math.max(1, bots));
    this._autostart = autostart;
    this._timers = [];
    this._players = [];       // { player_id, display_name, hand: [], connected }
    this._discard = [];
    this._drawn = 0;
    this._turnIdx = 0;
    this._phase = "lobby";    // lobby | playing
    this._turnPhase = "draw";
    this._turnNo = 0;
    this._name = "Ty";
    this._mode = ["rezba", "vydrz", "vydrz_rucne"].includes(mode) ? mode : "vydrz";    // lobby selection, like the real server
    this._vydrz = null;
  }

  // --- SocketClient API -------------------------------------------------

  connect(roomId, displayName) {
    this._name = displayName || "You";
    this._later(150, () => {
      bus.emit("socket:connected", {});
      this._players = [{ player_id: ME, display_name: this._name, hand: [], connected: true }];
      bus.emit("server:joined", { room_id: "DEMO", player_id: ME, display_name: this._name });
      this._emitRoomState();
      for (let i = 0; i < this._botCount; i++) {
        this._later(500 + i * 450, () => {
          const p = { player_id: `bot${i + 1}`, display_name: BOT_NAMES[i], hand: [], connected: true };
          this._players.push(p);
          this._emitRoomState();
          if (this._autostart && i === this._botCount - 1) this._later(200, () => (this._mode === "rezba" ? this._start() : this._startVydrz()));
        });
      }
    });
  }

  tryResume() { return false; }
  disconnect() { this._timers.forEach(clearTimeout); this._timers = []; this._vydrz?.stop(); }

  send(type, payload = {}) {
    const handler = {
      start_game: () => (this._mode === "rezba" ? this._start() : this._startVydrz()),
      set_mode: () => { if (MODES.some(m => m.id === payload.mode)) { this._mode = payload.mode; this._emitRoomState(); } },
      vydrz_plan: () => this._vydrzDo(() => this._vydrz?.setPlan?.(ME, (payload.pairs ?? []).map(p => [p.location_uid, p.existence_uid])), "vydrz_plan"),
      vydrz_submit: () => this._vydrzDo(() => this._vydrz?.submit?.(ME, payload.value), "vydrz_submit"),
      vydrz_choose: () => this._vydrzDo(() => this._vydrz?.choose(ME, payload.location_uid, payload.existence_uid), "vydrz_choose"),
      vydrz_next_round: () => this._vydrzDo(() => this._vydrz?.nextRound(), "vydrz_next_round"),
      draw_cards: () => this._draw(),
      play_card: () => this._play(payload.card_uid, payload.target_id),
      discard_card: () => this._discardFromHand(payload.card_uid),
      end_turn: () => this._endTurn(),
      request_snapshot: () => this._snapshot(),
      chat: () => this._chat(payload.message),
    }[type];
    if (handler) this._later(80, handler);   // small latency so pending states are visible
  }

  sendDrawCards()                     { this.send("draw_cards"); }
  sendPlayCard(cardUid, targetId = null) { this.send("play_card", { card_uid: cardUid, target_id: targetId }); }
  sendDiscardCard(cardUid)            { this.send("discard_card", { card_uid: cardUid }); }
  sendEndTurn()                       { this.send("end_turn"); }
  sendRequestSnapshot()               { this.send("request_snapshot"); }
  sendChat(message)                   { this.send("chat", { message }); }
  sendLeaveRoom()                     { this.disconnect(); }

  // --- fake server behaviour -------------------------------------------

  _emitRoomState() {
    bus.emit("server:room_state", {
      room_id: "DEMO", host_player_id: ME, game_phase: this._phase, max_players: 7,
      mode: this._mode,
      modes: MODES,
      players: this._players.map(p => ({ player_id: p.player_id, display_name: p.display_name, connected: p.connected })),
    });
  }

  _start() {
    if (this._phase === "playing" || this._players.length < 2) return;
    this._phase = "playing";
    this._players.forEach(p => { p.hand = Array.from({ length: STARTING_HAND }, makeCard); });
    this._drawn = this._players.length * STARTING_HAND;
    this._turnNo = 1;
    this._turnIdx = 0;
    this._turnPhase = "draw";
    bus.emit("server:action_ack", { action: "start_game" });
    bus.emit("server:game_event", { event_type: "game_started", data: { player_ids: this._players.map(p => p.player_id), mode: "demo" } });
    this._snapshot();
    this._announceTurn();
  }

  // --- Výdrž (see mock-vydrz.js) ---

  _startVydrz() {
    if (this._vydrz || this._players.length < 2) return;
    this._phase = "playing";
    const Engine = this._mode === "vydrz_rucne" ? MockManual : MockVydrz;
    this._vydrz = new Engine(
      this._players.map(p => ({ id: p.player_id, name: p.display_name })),
      () => this._emitVydrz(),
    );
    bus.emit("server:action_ack", { action: "start_game" });
    this._emitRoomState();
    this._emitVydrz();
  }

  _vydrzDo(fn, action) {
    const err = fn();
    if (err) { this._fail(err); return; }
    bus.emit("server:action_ack", { action });
  }

  _emitVydrz() {
    if (this._vydrz) bus.emit("server:vydrz_state", this._vydrz.snapshot(ME));
  }

  _announceTurn() {
    const p = this._players[this._turnIdx];
    bus.emit("server:game_event", { event_type: "turn_started", data: { player_id: p.player_id, turn_number: this._turnNo } });
    this._snapshot();
    if (p.player_id !== ME) this._botTurn(p);
  }

  _draw() {
    if (!this._isMyTurn() || this._turnPhase !== "draw") return this._fail("Nejsi ve fázi lízání.");
    const me = this._me();
    me.hand.push(makeCard(), makeCard());
    this._drawn += 2;
    this._turnPhase = "play";
    bus.emit("server:action_ack", { action: "draw_cards" });
    bus.emit("server:game_event", { event_type: "phase_changed", data: { player_id: ME, new_phase: "play" } });
    this._snapshot();
  }

  _play(uid, targetId) {
    if (!this._isMyTurn() || this._turnPhase !== "play") return this._fail("Nejsi ve fázi hraní.");
    const card = this._take(this._me(), uid);
    if (!card) return this._fail("Tuhle kartu nemáš v ruce.");
    this._discard.push(card);
    bus.emit("server:action_ack", { action: "play_card" });
    bus.emit("server:game_event", { event_type: "card_played", data: { player_id: ME, card_id: card.id, card_name: card.name, target_id: targetId ?? null } });
    this._snapshot();
  }

  _discardFromHand(uid) {
    const card = this._take(this._me(), uid);
    if (!card) return this._fail("Tuhle kartu nemáš v ruce.");
    this._discard.push(card);
    bus.emit("server:action_ack", { action: "discard_card" });
    this._snapshot();
  }

  _endTurn() {
    if (!this._isMyTurn()) return this._fail("Nejsi na tahu.");
    bus.emit("server:action_ack", { action: "end_turn" });
    this._nextTurn();
  }

  _nextTurn() {
    this._turnIdx = (this._turnIdx + 1) % this._players.length;
    this._turnNo += 1;
    this._turnPhase = "draw";
    this._announceTurn();
  }

  _botTurn(bot) {
    this._later(900, () => {
      bot.hand.push(makeCard(), makeCard());
      this._drawn += 2;
      this._turnPhase = "play";
      this._snapshot();
      this._later(900, () => {
        const card = bot.hand.splice(Math.floor(Math.random() * bot.hand.length), 1)[0];
        this._discard.push(card);
        const others = this._players.filter(p => p !== bot);
        bus.emit("server:game_event", { event_type: "card_played", data: { player_id: bot.player_id, card_id: card.id, card_name: card.name, target_id: _pick(others).player_id } });
        this._snapshot();
        this._later(800, () => this._nextTurn());
      });
    });
  }

  _chat(message) {
    bus.emit("server:chat", { sender_id: ME, display_name: this._name, message: String(message ?? "").slice(0, 256), timestamp: Date.now() / 1000 });
  }

  _fail(message) { bus.emit("server:error", { message, code: "mock_action_failed" }); }

  // --- state helpers ----------------------------------------------------

  _me() { return this._players.find(p => p.player_id === ME); }
  _isMyTurn() { return this._phase === "playing" && this._players[this._turnIdx]?.player_id === ME; }
  _take(player, uid) {
    const i = player.hand.findIndex(c => c.uid === uid);
    return i < 0 ? null : player.hand.splice(i, 1)[0];
  }

  _snapshot() {
    const me = this._me();
    const top = this._discard[this._discard.length - 1];
    bus.emit("server:state_snapshot", {
      state: {
        game_id: "mock", phase: "in_progress", mode: "demo",
        turn_number: this._turnNo, round_number: 1, winner_ids: [],
        turn: { player_id: this._players[this._turnIdx].player_id, phase: this._turnPhase, cards_drawn: 0 },
        deck: {
          draw_count: Math.max(0, DECK_SIZE - this._drawn),
          discard_count: this._discard.length,
          top_discard: top ? { id: top.id, name: top.name, suit: top.suit, rank: top.rank, has_art: false } : null,
        },
        players: this._players.map((p, i) => ({
          player_id: p.player_id, display_name: p.display_name,
          hp: MOCK_HP, max_hp: MOCK_HP, status: "alive", hand_count: p.hand.length,
          // One fake equipment badge so that layout can be judged.
          equipment: i === 1 ? [{ uid: "mock-eq", id: "placeholder", name: "Zástupný předmět", suit: "clubs", rank: "7" }] : [],
          role: null, connected: true,
        })),
      },
      your_player: { player_id: ME, hand: me.hand, hand_count: me.hand.length },
    });
  }

  _later(ms, fn) { this._timers.push(setTimeout(fn, ms)); }
}
