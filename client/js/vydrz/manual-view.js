/**
 * client/js/vydrz/manual-view.js
 *
 * Výdrž (ruční): the manual-counting variant. Separate from the automatic
 * view (view.js) so that changes here never affect it.
 *
 * Stages (from the server snapshot, "mode": "vydrz_rucne"):
 *   planning      arrange all 8 (location, existence) pairs, one per round
 *   calculating   3 drink cards are revealed one by one; the player counts
 *                 their number and types it on the on-screen number pad
 *   reveal        the table of results (with "Blbec" / "Švindlíř" tags)
 *   finished      final ranking
 *
 * The server knows the right number and decides what an entry means. This
 * file only collects input and shows what the server reports.
 */

import { bus } from "../core/events.js";
import { onLongPress } from "../utils/long-press.js";
import {
  cardBack, cardFront, cardMeta, characterFront, drinkFront, existenceFront, locationFront, drinkInfo, fmtStat,
} from "./cards.js";
import { dropButton } from "./view.js";
import { pilesHtml, onPileClick } from "./piles.js";
import { getSettings, updateSettings } from "./settings.js";
import * as sfx from "./sound.js";
import { showBaronSplash } from "./winner-splash.js";

const TAG_LABEL = { blbec: "Blbec", svindlir: "Švindlíř" };
const MAX_VALUE = 999;
const NUMPAD_ICON = `<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" fill="currentColor">${
  [4, 12, 20].flatMap(y => [4, 12, 20].map(x => `<circle cx="${x}" cy="${y}" r="2.1"/>`)).join("")}</svg>`;
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const signed = (n) => (n < 0 ? `−${Math.abs(n)}` : String(n));

export class ManualView {
  /** @param {{ socket, inspector, numpad, dealIntro, getHostId, onLeave, onConfirmLeave, openSettings }} deps */
  constructor({ socket, inspector, numpad, dealIntro, getHostId, onLeave, onConfirmLeave, openSettings }) {
    this._socket = socket;
    this._inspector = inspector;
    this._numpad = numpad;
    this._dealIntro = dealIntro;
    this._dealing = false;
    this._dealtUids = new Set();
    this._charLanded = true;
    this._dealt = false;
    this._getHostId = getHostId;
    this._onLeave = onLeave;
    this._confirmLeave = onConfirmLeave ?? (() => onLeave());
    this._openSettings = openSettings;
    this._state = null;

    this._plan = [];                       // local plan: [{location_uid, existence_uid}]
    this._sel = { lokace: null, existence: null };
    this._editing = false;                 // reopened a confirmed plan
    this._revealed = {};                   // draw_id -> number of drink cards turned over
    this._revealing = false;
    this._value = 0;                       // the number being built with -/+ or the number pad
    this._valueDraw = null;                // draw_id the value belongs to (reset on a new draw)
    this._fresh = true;                    // next typed digit replaces the value
    this._negPending = false;              // "-" typed before any digit (value is still 0)
    this._resultHidden = false;            // the player closed the results table (✕) to see the base view
    this._splashed = false;                // the "je Baron!" announcement was shown

    const $ = (id) => document.getElementById(id);
    this._el = { panel: $("vm-panel"), title: $("vm-title"), players: $("vm-players"), stage: $("vm-stage"), bar: $("vm-bar"), sheet: $("vy-sheet") };

    bus.on("server:vydrz_state", (s) => this._onState(s));
    this._wire();
  }

  // ------------------------------------------------------------------ wiring

  _wire() {
    const { stage, bar, players } = this._el;

    const wasLongPress = onLongPress(stage, ".vm-card[data-uid]", (el) => this._inspectNode(el));
    stage.addEventListener("click", (e) => {
      if (wasLongPress()) return;
      if (e.target.closest("[data-shuffle]")) { this._randomPlan(); return; }
      const pc = e.target.closest(".vm-pcard");
      if (pc) { if (!this._justDragged) this._inspectPlanned(pc.dataset.uid); return; }
      if (e.target.closest("[data-close-result]")) { this._resultHidden = true; this.render(); return; }
      const step = e.target.closest("[data-step]");
      if (step) { this._step(parseInt(step.dataset.step, 10)); return; }
      if (e.target.closest("[data-numpad-open]")) { this._openNumpad(); return; }
      if (e.target.closest("[data-submit]")) { this._submitValue(); return; }
      const inspect = e.target.closest("[data-inspect]");
      if (inspect) { this._inspectNode(inspect.closest(".vy-slot, .vm-card")); return; }
      const slot = e.target.closest(".vy-slot[data-kind]");
      if (slot) { this._select(slot.dataset.kind, slot.dataset.uid); return; }
      const card = e.target.closest(".vm-card[data-uid]");
      if (card) this._inspectNode(card);
    });
    stage.addEventListener("contextmenu", (e) => {
      const el = e.target.closest(".vy-slot, .vm-card");
      if (el) { e.preventDefault(); this._inspectNode(el); }
    });
    this._wireReorder(stage);
    this._wireCardSwap(stage);
    onPileClick(stage, () => this._state, (cards, uid) => this._inspectList(cards, uid));
    players.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-pid]");
      if (chip) this._openPlayerSheet(chip.dataset.pid);
    });
    bar.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn || btn.disabled) return;
      sfx.unlockAudio();
      ({ add: () => this._addPair(), undo: () => this._undo(), confirm: () => this._confirmPlan(),
         edit: () => { this._editing = true; this.render(); },
         next: () => this._socket.send("vydrz_next_round"),
         showresult: () => { this._resultHidden = false; this.render(); },
         announce: () => this._socket.send("vydrz_announce"),
         lobby: () => this._socket.send("return_to_lobby"),
         leave: () => this._onLeave() })[btn.dataset.act]?.();
    });
    document.addEventListener("keydown", (e) => this._onKey(e));
    document.getElementById("vm-settings-btn")?.addEventListener("click", () => this._openSettings());
    document.getElementById("vm-leave-btn")?.addEventListener("click", () => this._confirmLeave());
  }

  /**
   * Drag and drop (mouse and touch) to reorder the planned pairs: grab the ⠿ handle,
   * the row follows the pointer and the others make room; releasing drops it there.
   */
  _wireReorder(stage) {
    stage.addEventListener("pointerdown", (e) => {
      const handle = e.target.closest(".vm-grab");
      const row = handle?.closest("li[data-i]");
      if (!row || this._planLocked() || e.button > 0) return;
      e.preventDefault();
      const list = row.parentElement;
      const rows = [...list.querySelectorAll("li.is-set")];
      const from = rows.indexOf(row);
      const rects = rows.map(r => r.getBoundingClientRect());
      const step = rects.length > 1 ? rects[1].top - rects[0].top : rects[0].height;
      const startY = e.clientY;
      let to = from;
      handle.setPointerCapture?.(e.pointerId);
      row.classList.add("is-dragging");
      list.classList.add("is-reordering");
      const move = (ev) => {
        const dy = ev.clientY - startY;
        const centre = rects[from].top + rects[from].height / 2 + dy;
        const idx = rects.findIndex(r => centre < r.top + r.height);
        to = idx === -1 ? rows.length - 1 : idx;
        row.style.transform = `translateY(${dy}px)`;
        rows.forEach((r, i) => {
          if (r === row) return;
          const shift = from < to && i > from && i <= to ? -step : from > to && i >= to && i < from ? step : 0;
          r.style.transform = shift ? `translateY(${shift}px)` : "";
        });
      };
      const end = (ev) => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", end);
        handle.removeEventListener("pointercancel", end);
        if (ev.type === "pointerup" && to !== from) {
          const [moved] = this._plan.splice(from, 1);
          this._plan.splice(to, 0, moved);
          sfx.pop();
        }
        this.render();
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", end);
      handle.addEventListener("pointercancel", end);
    });
  }

  /**
   * Drag one card (location or existence) of the plan onto the same kind of card in another
   * row to swap them, so a single change does not mean redoing the whole table. A tap
   * without movement inspects the card. With touch, hold briefly before dragging so the
   * page can still be scrolled.
   */
  _wireCardSwap(stage) {
    stage.addEventListener("pointerdown", (e) => {
      const src = e.target.closest(".vm-pcard.is-movable");
      if (!src || this._planLocked() || e.button > 0) return;
      const kind = src.dataset.kind, from = +src.dataset.i;
      const touch = e.pointerType === "touch";
      const sx = e.clientX, sy = e.clientY;
      let ghost = null, over = null, armed = !touch, holdTimer = null, done = false;
      const cellUnder = (x, y) => {
        const el = document.elementFromPoint(x, y)?.closest(`.vm-pcard[data-kind="${kind}"]`);
        return el && +el.dataset.i !== from ? el : null;
      };
      const start = () => {
        ghost = document.createElement("div");
        ghost.className = "vm-ghost";
        ghost.textContent = src.textContent;
        document.body.appendChild(ghost);
        src.classList.add("is-lifted");
      };
      const place = (x, y) => { ghost.style.transform = `translate(${x + 10}px, ${y + 10}px)`; };
      const block = (ev) => { if (ghost) ev.preventDefault(); };      // keep the page still while dragging on touch
      const finish = (commit) => {
        if (done) return;
        done = true;
        clearTimeout(holdTimer);
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        document.removeEventListener("pointercancel", cancel);
        document.removeEventListener("touchmove", block);
        over?.classList.remove("is-target");
        src.classList.remove("is-lifted");
        ghost?.remove();
        if (commit && over) {
          const to = +over.dataset.i;
          const key = kind === "lokace" ? "location_uid" : "existence_uid";
          [this._plan[from][key], this._plan[to][key]] = [this._plan[to][key], this._plan[from][key]];
          sfx.pop();
        }
        if (ghost) {                                        // (a plain tap must not re-render: it would swallow the click)
          this._justDragged = true; setTimeout(() => { this._justDragged = false; }, 0);
          this.render();
        }
      };
      const move = (ev) => {
        if (!ghost) {
          const dist = Math.hypot(ev.clientX - sx, ev.clientY - sy);
          if (touch && !armed) { if (dist > 8) finish(false); return; }       // moved before the hold: it is a scroll
          if (dist < 6) return;
          start();
        }
        place(ev.clientX, ev.clientY);
        const t = cellUnder(ev.clientX, ev.clientY);
        if (t !== over) { over?.classList.remove("is-target"); over = t; over?.classList.add("is-target"); }
      };
      const up = () => finish(true);
      const cancel = () => finish(false);
      if (touch) holdTimer = setTimeout(() => { armed = true; start(); place(sx, sy); navigator.vibrate?.(15); }, 280);
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
      document.addEventListener("pointercancel", cancel);
      document.addEventListener("touchmove", block, { passive: false });
    });
  }

  /** Random arrangement of all pairs; only a starting point the player can still change by hand. */
  _randomPlan() {
    if (this._planLocked()) return;
    const me = this._me();
    const shuffled = (list) => {
      const a = [...list];
      for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
      return a;
    };
    const locs = shuffled(me.locations), exs = shuffled(me.existences);
    this._plan = locs.map((l, i) => ({ location_uid: l.uid, existence_uid: exs[i].uid }));
    this._sel = { lokace: null, existence: null };
    sfx.unlockAudio(); sfx.pop();
    this.render();
  }

  _inspectPlanned(uid) {
    const me = this._me();
    const card = [...me.locations, ...me.existences].find(c => c.uid === uid);
    if (!card) return;
    this._inspectList(card.kind === "lokace" ? me.locations : me.existences, uid);
  }

  _inspectList(cards, uid) {
    const items = cards.map(c => ({ uid: c.uid, html: cardFront(c, { extra: "vc--big" }), name: c.name, meta: cardMeta(c), desc: c.description ?? "" }));
    this._inspector.openCustom(items, Math.max(0, items.findIndex(i => i.uid === uid)));
  }

  // ------------------------------------------------------------------- state

  _me() { return this._state.players.find(p => p.player_id === this._state.you); }
  _p(pid) { return this._state.players.find(p => p.player_id === pid); }
  _names() { return Object.fromEntries(this._state.players.map(p => [p.player_id, p.display_name])); }
  _isHost() { return this._getHostId() === this._state?.you; }
  isHost() { return this._isHost(); }

  _onState(state) {
    if (state.mode !== "vydrz_rucne") return;
    const first = !this._state;                     // first state since the page loaded / the game began
    const prevPhase = this._state?.phase;
    const wasAnnounced = this._state?.winner_announced ?? false;
    if (prevPhase !== state.phase || wasAnnounced !== state.winner_announced || state.round !== this._state?.round) this._resultHidden = false;
    this._state = state;
    ["join-panel", "lobby-panel", "game-panel", "vydrz-panel"].forEach(id => { const el = document.getElementById(id); if (el) el.hidden = true; });
    this._el.panel.hidden = false;

    const me = this._me();
    if (state.phase === "planning") {
      // Take the server's plan as the starting point (e.g. after a reconnect).
      if (me.plan && !this._editing && this._plan.length === 0) this._plan = me.plan.map(x => ({ ...x }));
    } else if (prevPhase === "planning") {
      this._editing = false;
    }
    // Returning to a game in progress: no deal replay, no drink turn-over replay.
    if (first && (state.phase !== "planning" || me.planned || me.plan?.length)) this._dealt = true;
    if (first && state.calc) this._revealed[state.calc.draw_id] = state.calc.drinks.length;
    if (first && state.winner_announced) this._splashed = true;
    const deal = state.phase === "planning" && !this._dealt && getSettings().deal;
    if (state.phase === "planning") this._dealt = true;
    if (deal) { this._dealing = true; this._charLanded = false; this._dealtUids.clear(); }
    this.render();
    if (deal) requestAnimationFrame(() => this._runDeal());
    this._maybeRevealDrinks();
    const final = state.phase === "finished" && state.winner_announced ? state.last_result?.final : null;
    if (final && !this._splashed && !wasAnnounced) {
      this._splashed = true;
      showBaronSplash({ name: final.winner_id ? (this._names()[final.winner_id] ?? final.winner_id) : null, character: final.winner_id ? this._p(final.winner_id)?.character?.name : null, isMe: final.winner_id === state.you });
    }
  }

  /** Back to the pre-game lobby: forget everything about the finished game. */
  reset() {
    this._state = null;
    this._plan = [];
    this._sel = { lokace: null, existence: null };
    this._editing = false;
    this._revealed = {};
    this._revealing = false;
    this._dealt = false;
    this._dealing = false;
    this._splashed = false;
    this._resultHidden = false;
    this._value = 0;
    this._el.sheet.hidden = true;
    this._el.panel.hidden = true;
    this._el.stage.innerHTML = "";
    this._el.bar.innerHTML = "";
  }

  /** The 16 cards are shown pair by pair (see deal-intro.js), then land in the hand. */
  async _runDeal() {
    const me = this._me();
    const slotOf = (kind, uid) => this._el.stage.querySelector(`.vy-slot[data-uid="${CSS.escape(uid)}"]`);
    const charSlot = () => this._el.stage.querySelector(".vm-char");
    await this._dealIntro.play({
      character: me.character,
      getCharSlot: charSlot,
      onLandChar: () => {
        this._charLanded = true;
        const el = charSlot();
        if (el) { el.classList.remove("is-undealt"); el.classList.add("is-dealt-pop"); }
      },
      pairs: me.locations.map((l, i) => ({ lokace: l, existence: me.existences[i] })),
      counts: { postava: this._state.decks.postava, lokace: this._state.decks.lokace, existence: this._state.decks.existence },
      getSlot: slotOf,
      onLand: (uid) => {
        this._dealtUids.add(uid);
        const slot = slotOf("", uid);
        if (slot) { slot.classList.remove("is-undealt"); slot.classList.add("is-dealt-pop"); }
      },
      prepare: () => {
        const scroller = this._el.stage.closest(".vy-scroll");
        const section = this._el.stage.querySelector(".vy-hand-sec");
        if (scroller && section) scroller.scrollTop = section.offsetTop - 8;
      },
    });
    this._dealing = false;
    this._charLanded = true;
    if (this._state?.phase === "planning") this.render();
  }

  render() {
    const s = this._state;
    if (!s) return;
    this._el.title.textContent = ({
      planning: "Plánování", calculating: `Kolo ${s.round} / ${s.rounds_total}`,
      reveal: `Kolo ${s.round} / ${s.rounds_total}`, finished: "Konec hry",
    })[s.phase] ?? "Výdrž";
    this._renderPlayers();
    this._el.stage.innerHTML = ({
      planning: () => this._stagePlanning(), calculating: () => this._stageCalculating(),
      reveal: () => this._showsResult() ? this._stageReveal() : this._stageBase(),
      finished: () => this._showsResult() ? this._stageFinal() : this._stageBase(),
    })[s.phase]?.() ?? "";
    this._renderBar();
    this._paintDrinks();
  }

  // ----------------------------------------------------------------- players

  _renderPlayers() {
    const s = this._state;
    const calc = s.calc;
    this._el.players.innerHTML = s.players.map((p) => {
      let status = "";
      if (p.away) status = "odešel(a)";
      else if (p.connected === false) status = "odpojen(a)";
      else if (s.phase === "planning") status = p.planned ? "✔ plán hotov" : "plánuje…";
      else if (s.phase === "calculating" && calc?.participants.includes(p.player_id))
        status = calc.entries[p.player_id] ? "✔ zadáno" : "počítá…";
      else if (p.won.length) status = `${p.won.length}× lokace`;
      const done = (s.phase === "planning" && p.planned) || (calc && calc.entries[p.player_id]);
      return `<button class="vy-player${p.player_id === s.you ? " is-me" : ""}${done ? " is-locked" : ""}" type="button" data-pid="${esc(p.player_id)}">
        <span class="vy-player__name">${esc(p.display_name)}</span>
        <span class="vy-player__score">${p.score}<small> b.</small></span>
        <span class="vy-player__state">${status}</span></button>`;
    }).join("");
  }

  // ---------------------------------------------------------------- planning

  _remaining(kind) {
    const me = this._me();
    const used = new Set(this._plan.map(x => (kind === "lokace" ? x.location_uid : x.existence_uid)));
    return (kind === "lokace" ? me.locations : me.existences).filter(c => !used.has(c.uid));
  }

  _planLocked() { return this._me().planned && !this._editing; }

  _stagePlanning() {
    const me = this._me();
    const byUid = Object.fromEntries([...me.locations, ...me.existences].map(c => [c.uid, c]));
    const rows = Array.from({ length: this._state.rounds_total }, (_, i) => {
      const pair = this._plan[i];
      const unlocked = !this._planLocked();
      const cell = (cls, kind, card) => `<span class="${cls} vm-pcard${unlocked ? " is-movable" : ""}" data-kind="${kind}" data-i="${i}" data-uid="${esc(card.uid)}" title="Klepni pro zvětšení${unlocked ? ", přetáhni pro prohození" : ""}">${esc(card.name)}${card.number ? ` <b>(${card.number})</b>` : ""}</span>`;
      return pair
        ? `<li class="is-set${unlocked ? " is-draggable" : ""}" data-i="${i}">${unlocked ? `<span class="vm-grab" aria-label="Přetáhni celou dvojici" title="Přetáhni celou dvojici">⠿</span>` : ""}<span class="vm-n">${i + 1}</span>${cell("vm-plan__loc", "lokace", byUid[pair.location_uid])}${cell("vm-plan__ex", "existence", byUid[pair.existence_uid])}</li>`
        : `<li><span class="vm-n">${i + 1}</span><span class="vm-plan__empty">—</span></li>`;
    }).join("");

    const hand = (kind, label, front) => {
      const cards = this._remaining(kind);
      const locked = this._planLocked();
      return `<section class="vy-hand-sec"><h3>${label} (${cards.length})</h3><div class="vy-hand">${
        cards.map((c) => {
          const sel = this._sel[kind] === c.uid;
          const undealt = this._dealing && !this._dealtUids.has(c.uid);
          return `<div class="vy-slot${sel ? " is-selected" : ""}${locked ? " is-locked" : ""}${undealt ? " is-undealt" : ""}" data-kind="${kind}" data-uid="${esc(c.uid)}">
            ${front(c, { extra: "vc--hand vm-card" })}
            ${sel ? `<button class="vy-inspect" type="button" data-inspect aria-label="Zvětšit kartu">🔍</button>` : ""}
          </div>`;
        }).join("") || `<p class="vy-empty">Hotovo.</p>`}</div></section>`;
    };

    return `
      <section class="vm-me"><div class="vm-char${this._charLanded ? "" : " is-undealt"}">${characterFront(me.character, { extra: "vc--mini vm-card" })}</div>
        <div class="vm-me__text"><h3>${esc(me.display_name)}</h3><p>Tvoje postava. Klepnutím ji zvětšíš.</p></div></section>
      <p class="vm-info">Rozděl si <b>všech ${this._state.rounds_total} dvojic</b> předem. Dvojice č. 1 se použije v 1. kole, č. 2 ve 2. a tak dál. Ostatní uvidí tvé karty, ne pořadí.</p>
      <section class="vm-plan"><div class="vm-plan__head"><h3>Tvůj plán</h3>${this._planLocked() ? "" : `<button class="btn btn--ghost btn--small" type="button" data-shuffle>Rozdělit náhodně</button>`}</div><ol class="vm-plan__list">${rows}</ol></section>
      ${hand("lokace", "Lokace", locationFront)}
      ${hand("existence", "Existence", existenceFront)}`;
  }

  _select(kind, uid) {
    if (this._state.phase !== "planning" || this._planLocked()) return;
    this._sel[kind] = this._sel[kind] === uid ? null : uid;
    sfx.unlockAudio(); sfx.pop();
    this._autoSelectLast();
    this.render();
  }

  /** When only one card of a kind is left there is nothing to choose. */
  _autoSelectLast() {
    for (const kind of ["lokace", "existence"]) {
      const left = this._remaining(kind);
      if (left.length === 1 && this._plan.length < this._state.rounds_total) this._sel[kind] = left[0].uid;
    }
  }

  _addPair() {
    if (!(this._sel.lokace && this._sel.existence)) return;
    this._plan.push({ location_uid: this._sel.lokace, existence_uid: this._sel.existence });
    this._sel = { lokace: null, existence: null };
    sfx.pop();
    this._autoSelectLast();
    this.render();
  }

  _undo() {
    this._plan.pop();
    this._sel = { lokace: null, existence: null };
    this.render();
  }

  _confirmPlan() {
    if (this._plan.length !== this._state.rounds_total) return;
    this._editing = false;
    this._socket.send("vydrz_plan", { pairs: this._plan });
  }

  // ------------------------------------------------------------- calculating

  _stageCalculating() {
    const s = this._state, c = s.calc, me = this._me();
    if (!c) return "";
    const mine = c.participants.includes(s.you);
    const title = ({
      round: `Kolo ${s.round}: spočítej své číslo`,
      final_first: "Rozstřel o 1. místo",
      final_last: "Rozstřel o poslední místo",
    })[c.purpose] ?? "";
    const note = c.tiebreak
      ? `Remíza! Počítá se <b>jen tvoje postava</b> a vytažený chlast${mine ? "" : `. Rozstřel řeší: ${c.participants.map(id => esc(this._p(id).display_name)).join(", ")}.`}`
      : `Sečti staty vytažených chlastů na <b>postavě, lokaci a existenci</b> a zadej číslo.`;

    const cardsMine = mine ? `<section class="vm-mine"><h3>${c.tiebreak ? "Tvoje postava" : "Tvoje karty"}</h3><div class="vm-mine__cards">
        ${characterFront(me.character, { extra: "vc--mini vm-card" })}
        ${!c.tiebreak && c.mine ? `${locationFront(c.mine.location, { extra: "vc--mini vm-card" })}${existenceFront(c.mine.existence, { extra: "vc--mini vm-card" })}` : ""}
      </div></section>` : "";

    const entered = c.entries[s.you];
    const canType = mine && !entered && !this._revealing;
    if (this._valueDraw !== c.draw_id) { this._valueDraw = c.draw_id; this._value = 0; this._fresh = true; }
    const shown = entered ? entered.value : this._value;
    const field = mine ? `<section class="vm-entry"><h3>Tvoje číslo</h3>
        <div class="vm-stepper${canType ? "" : " is-off"}">
          <button class="vm-step" type="button" data-step="-1" aria-label="Mínus 1" ${canType ? "" : "disabled"}>\u2212</button>
          <div class="vm-value" data-value role="status" aria-live="polite">${esc(signed(shown))}</div>
          <button class="vm-step" type="button" data-step="1" aria-label="Plus 1" ${canType ? "" : "disabled"}>+</button>
          <button class="vm-numpad-btn" type="button" data-numpad-open aria-label="Otevřít číselník" ${canType ? "" : "disabled"}>${NUMPAD_ICON}</button>
        </div>
        <button class="btn btn--block vm-enter" type="button" data-submit ${canType ? "" : "disabled"}>${
          entered ? "Odesláno" : this._revealing ? "Počkej na chlast…" : "Potvrdit ↵"}</button>
      </section>` : "";

    const rows = c.participants.map((pid) => {
      const e = c.entries[pid];
      return `<li class="vm-row${e ? "" : " is-pending"}"><span class="vm-row__name">${esc(this._p(pid).display_name)}</span>
        <span class="vm-row__val">${e ? signed(e.value) : "počítá…"}</span>${e?.tag ? this._tag(e.tag) : ""}</li>`;
    }).join("");

    return `
      <h2 class="vm-banner">${title}</h2>
      <p class="vm-info">${note}</p>
      <section class="vm-drinks"><h3>${c.drinks.length > 1 ? "Vytažený chlast" : "Vytažená karta chlastu"}</h3><div class="vm-drinks__cards" data-draw="${c.draw_id}"></div></section>
      ${cardsMine}${field}
      <section class="vm-table"><h3>Výsledky</h3><ul class="vm-rows">${rows}</ul></section>`;
  }

  _tag(tag) { return `<span class="vm-tag vm-tag--${tag}">${TAG_LABEL[tag]}</span>`; }

  _paintDrinks() {
    const box = this._el.stage.querySelector(".vm-drinks__cards");
    const c = this._state?.calc;
    if (!box || !c) return;
    const shown = this._revealed[c.draw_id] ?? 0;
    box.innerHTML = c.drinks.map((d, i) => `<div class="vm-drink${i === shown - 1 && this._revealing ? " is-new" : ""}">${i < shown ? drinkFront(d, { extra: "vm-card" }) : cardBack("chlast")}</div>`).join("");
  }

  /** Turn the drawn drink cards over one at a time, once per draw. */
  async _maybeRevealDrinks() {
    const c = this._state?.calc;
    if (!c || this._state.phase !== "calculating") return;
    if ((this._revealed[c.draw_id] ?? 0) >= c.drinks.length || this._revealing) return;
    const drawId = c.draw_id;
    this._revealing = true;
    this.render();
    for (let i = 1; i <= c.drinks.length; i++) {
      await new Promise(r => setTimeout(r, i === 1 ? (c.tiebreak ? 1400 : 500) : 750));
      if (this._state?.calc?.draw_id !== drawId) { this._revealing = false; return; }
      this._revealed[drawId] = i;
      sfx.flip();
      this._paintDrinks();
    }
    await new Promise(r => setTimeout(r, 350));
    this._revealing = false;
    this.render();
  }

  /** True while the player may change / send their number. */
  _canEnter() {
    const c = this._state?.calc;
    return !!c && this._state.phase === "calculating" && c.participants.includes(this._state.you) && !c.entered && !this._revealing;
  }

  _paintValue() {
    const el = this._el.stage.querySelector("[data-value]");
    if (el) el.textContent = this._negPending && this._value === 0 ? "\u22120" : signed(this._value);
  }

  _step(delta) {
    if (!this._canEnter()) return;
    this._value = Math.max(-MAX_VALUE, Math.min(MAX_VALUE, this._value + delta));
    this._fresh = true; this._negPending = false;
    sfx.unlockAudio(); sfx.stepSound(delta, this._value);
    this._paintValue();
  }

  _openNumpad() {
    if (!this._canEnter()) return;
    sfx.unlockAudio();
    const c = this._state.calc;
    this._numpad.open({
      title: "Tvoje číslo",
      hint: c.tiebreak ? "Stat tvé postavy u vytaženého chlastu." : "Součet statů všech tří chlastů na tvých kartách.",
      initial: this._value,
      onSubmit: (n) => {                      // Enter on the pad sends the number right away
        this._value = n; this._fresh = true; this._paintValue();
        this._submitValue();
      },
    });
  }

  _submitValue() {
    if (!this._canEnter()) return;
    sfx.unlockAudio();
    this._socket.send("vydrz_submit", { value: this._value });
  }

  /** Physical keyboard (computers): digits, -, Backspace, arrows and Enter. */
  _onKey(e) {
    if (this._el.panel.hidden || !this._canEnter() || this._numpad.isOpen) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName ?? "")) return;
    if (["card-inspector", "vy-sheet", "vy-settings"].some(id => document.getElementById(id)?.hidden === false)) return;
    const k = e.key;
    if (/^[0-9]$/.test(k)) {
      const neg = this._value < 0 || this._negPending;
      this._negPending = false;
      const mag = this._fresh ? 0 : Math.abs(this._value);
      const next = Math.min(MAX_VALUE, mag * 10 + parseInt(k, 10));
      this._value = neg ? -next : next;
      this._fresh = false;
    } else if (k === "-") {
      this._step(-1); e.preventDefault(); return;                       // like the − button
    } else if (k === "_") {                                              // Shift+minus: flip the sign, so negative numbers can be typed
      if (this._value === 0) this._negPending = !this._negPending;
      else this._value = -this._value;
      this._fresh = false;
    } else if (k === "Backspace") {
      this._value = Math.trunc(this._value / 10); this._fresh = false;
    } else if (k === "ArrowUp" || k === "ArrowRight" || k === "+" || k === "=") {
      this._step(1); e.preventDefault(); return;
    } else if (k === "ArrowDown" || k === "ArrowLeft") {
      this._step(-1); e.preventDefault(); return;
    } else if (k === "Enter") {
      e.preventDefault(); this._submitValue(); return;
    } else return;
    e.preventDefault();
    sfx.unlockAudio(); sfx.pop();
    this._paintValue();
  }

  // ------------------------------------------------------------------ reveal

  _resultTable(r) {
    const names = this._names();
    return `<ul class="vm-rows vm-rows--result">${Object.entries(r.table).map(([pid, t]) => {
      const pick = r.picks[pid];
      return `<li class="vm-row${pid === r.winner_id ? " is-winner" : ""}">
        <span class="vm-row__name">${esc(names[pid] ?? pid)}<small>${esc(pick.lokace.name)} · ${esc(pick.existence.name)}</small></span>
        <span class="vm-row__val">${signed(t.value)}</span>${t.tag ? this._tag(t.tag) : ""}
        <span class="vm-row__right">správně: <b>${signed(t.correct)}</b></span></li>`;
    }).join("")}</ul>`;
  }

  _tiebreakNotes(steps) {
    const names = this._names();
    return steps.map((st) => `<p class="vm-tiebreak">Rozstřel (${esc(drinkInfo(st.drinks[0]).name)}): ${Object.entries(st.table).map(([pid, t]) =>
      `${esc(names[pid] ?? pid)} <b>${signed(t.value)}</b>${t.tag ? ` ${this._tag(t.tag)}` : ""}`).join(" · ")}</p>`).join("");
  }

  _showsResult() { return !this._resultHidden; }

  /** The base view while a result is closed: your character and the cards you still have. */
  _stageBase() {
    const me = this._me();
    const hand = (cards, label, front) => `<section class="vy-hand-sec"><h3>${label} (${cards.length})</h3><div class="vy-hand">${
      cards.map(c => `<div class="vy-slot is-locked">${front(c, { extra: "vc--hand vm-card" })}</div>`).join("") || `<p class="vy-empty">Žádné karty.</p>`}</div></section>`;
    return `
      <section class="vy-table">${pilesHtml(this._state)}</section>
      <section class="vm-me"><div class="vm-char">${characterFront(me.character, { extra: "vc--mini vm-card" })}</div>
        <div class="vm-me__text"><h3>${esc(me.display_name)}</h3><p>Tvoje postava. Klepnutím ji zvětšíš.</p>
          ${me.won.length ? `<p class="vy-me__won">Získané lokace: ${me.won.map(l => `<b>${l.number}</b>`).join(" ")}</p>` : ""}</div></section>
      ${hand(me.locations, "Lokace", locationFront)}
      ${hand(me.existences, "Existence", existenceFront)}`;
  }

  _stageReveal() {
    const r = this._state.last_result;
    if (!r) return "";
    const names = this._names();
    const win = r.winner_id
      ? `<b>${esc(names[r.winner_id])}</b> bere lokaci <b>${esc(r.picks[r.winner_id].lokace.name)}</b> a získává <b class="vr__plus">+${r.awarded}</b> b.`
      : "Remíza se nepodařilo rozhodnout. Lokaci nikdo nezíská.";
    return `
      <div class="vm-bannerrow"><h2 class="vm-banner">Kolo ${r.round}: výsledek</h2><button class="hud__btn" type="button" data-close-result aria-label="Skrýt výsledek">✕</button></div>
      <section class="vm-drinks"><h3>Vytažený chlast</h3><div class="vm-drinks__cards vm-drinks__cards--done">${r.drawn.map(d => `<div class="vm-drink">${drinkFront(d)}</div>`).join("")}</div></section>
      <section class="vm-table"><h3>Výsledky</h3>${this._resultTable(r)}${this._tiebreakNotes(r.tiebreak)}</section>
      <div class="vr__outcome">${win}</div>`;
  }

  _stageFinal() {
    const r = this._state.last_result;
    const f = r?.final;
    if (!f) return this._stageReveal();            // the winner has not been announced yet: just the last round
    const names = this._names();
    const label = (pid) => (pid === f.winner_id ? "Vítěz" : pid === f.loser_id ? "Poražený" : "");
    return `
      <div class="vm-bannerrow"><h2 class="vm-banner">Konec hry</h2><button class="hud__btn" type="button" data-close-result aria-label="Skrýt výsledek">✕</button></div>
      <ol class="vr__ranking">${f.ranking.map(x => `<li class="${x.player_id === f.winner_id ? "is-winner" : x.player_id === f.loser_id ? "is-loser" : ""}">
        <span class="vr__place">${x.place}.</span><span class="vr__rname">${esc(names[x.player_id] ?? x.player_id)}</span><b>${x.score} b.</b><em>${label(x.player_id)}</em></li>`).join("")}</ol>
      ${this._tiebreakNotes(f.tiebreak_first)}${this._tiebreakNotes(f.tiebreak_last)}
      <section class="vm-table"><h3>Poslední kolo</h3>${this._resultTable(r)}</section>`;
  }

  // --------------------------------------------------------------------- bar

  _renderBar() {
    const s = this._state, me = this._me();
    let html = "";
    if (s.phase === "planning") {
      const total = s.rounds_total;
      if (this._planLocked()) {
        const done = s.players.filter(p => p.planned).length;
        html = `<div class="vy-bar__row"><span class="vy-wait">Plán odeslán. Čeká se na ostatní (${done}/${s.players.length})</span>
          <button class="btn btn--ghost btn--small" data-act="edit" type="button">Upravit plán</button></div>`;
      } else if (this._plan.length === total) {
        html = `<div class="vy-bar__row"><button class="btn btn--ghost btn--small" data-act="undo" type="button">Zpět</button>
          <button class="btn vm-grow" data-act="confirm" type="button">Potvrdit plán</button></div>`;
      } else {
        const ready = this._sel.lokace && this._sel.existence;
        html = `<div class="vy-bar__row"><button class="btn btn--ghost btn--small" data-act="undo" type="button" ${this._plan.length ? "" : "disabled"}>Zpět</button>
          <button class="btn vm-grow" data-act="add" type="button" ${ready ? "" : "disabled"}>${ready ? `Přidat dvojici (${this._plan.length + 1}/${total})` : "Vyber lokaci i existenci"}</button></div>`;
      }
    } else if (s.phase === "reveal") {
      html = this._isHost() ? `<button class="btn btn--block" data-act="next" type="button">Další kolo</button>`
                            : `<p class="vr__waiting">Čeká se, až host spustí další kolo…</p>`;
    } else if (s.phase === "finished" && !s.winner_announced) {
      html = this._isHost() ? `<button class="btn btn--block" data-act="announce" type="button">Ohlásit vítěze</button>`
                            : `<p class="vr__waiting">Prohlédni si výsledky. Čeká se, až host ohlásí vítěze…</p>`;
    } else if (s.phase === "finished") {
      const host = this._isHost();
      html = `<button class="btn btn--block" data-act="leave" type="button">Opustit hru</button>
        <button class="btn btn--block btn--ghost" data-act="lobby" type="button" ${host ? "" : "disabled"}>Zpět do lobby</button>
        ${host ? "" : `<p class="vr__waiting">Do lobby vrací všechny host.</p>`}`;
    }
    if ((s.phase === "reveal" || s.phase === "finished") && this._resultHidden) {
      html = `<button class="btn btn--ghost btn--block" data-act="showresult" type="button">Zobrazit výsledek kola</button>` + html;
    }
    this._el.bar.innerHTML = html;
  }

  // ------------------------------------------------------- inspect / sheets

  _inspectNode(el) {
    if (!el) return;
    const uid = el.dataset.uid ?? el.querySelector("[data-uid]")?.dataset.uid;
    const pool = this._cardPool();
    const items = pool.map(c => ({ uid: c.uid, html: cardFront(c, { extra: "vc--big" }), name: c.name, meta: cardMeta(c), desc: c.description ?? "" }));
    const index = items.findIndex(i => i.uid === uid);
    if (index >= 0) this._inspector.openCustom(items, index);
  }

  /** Cards that can be inspected here, in a sensible order. */
  _cardPool() {
    const me = this._me(), c = this._state.calc;
    const pool = [me.character];
    if (c?.mine) pool.push(c.mine.location, c.mine.existence);
    pool.push(...this._remaining("lokace"), ...this._remaining("existence"));
    return pool;
  }

  _openPlayerSheet(pid) {
    const p = this._p(pid);
    const sheet = this._el.sheet;
    sheet.innerHTML = `
      <div class="vs__backdrop" data-close></div>
      <div class="vs__panel">
        <header class="vs__head"><h2>${esc(p.display_name)} <small>${p.score} b.</small></h2>
          <button class="hud__btn" data-close type="button" aria-label="Zavřít">✕</button></header>
        ${dropButton(this, { ...p })}
        <p class="vs__note">${p.planned ? "Pořadí dvojic je skryté do vyhodnocení jednotlivých kol." : "Ještě plánuje."}</p>
        <h3>Postava</h3><div class="vs__row">${characterFront(p.character, { extra: "vc--mini" })}</div>
        <h3>Lokace (${p.locations.length})</h3><div class="vs__row">${p.locations.map(c => locationFront(c, { extra: "vc--mini" })).join("") || "<p class='vy-empty'>—</p>"}</div>
        <h3>Existence (${p.existences.length})</h3><div class="vs__row">${p.existences.map(c => existenceFront(c, { extra: "vc--mini" })).join("") || "<p class='vy-empty'>—</p>"}</div>
        ${p.won.length ? `<h3>Získané lokace</h3><div class="vs__row">${p.won.map(c => locationFront(c, { extra: "vc--mini" })).join("")}</div>` : ""}
      </div>`;
    sheet.hidden = false;
    sheet.onclick = (e) => {
      if (e.target.closest("[data-close]")) { sheet.hidden = true; return; }
      const drop = e.target.closest("[data-drop]");
      if (drop) { this._socket.send("drop_player", { player_id: drop.dataset.drop }); sheet.hidden = true; }
    };
  }
}
