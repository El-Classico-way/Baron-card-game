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
import { getSettings, updateSettings } from "./settings.js";
import * as sfx from "./sound.js";

const TAG_LABEL = { blbec: "Blbec", svindlir: "Švindlíř" };
const MAX_VALUE = 999;
const NUMPAD_ICON = `<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" fill="currentColor">${
  [4, 12, 20].flatMap(y => [4, 12, 20].map(x => `<circle cx="${x}" cy="${y}" r="2.1"/>`)).join("")}</svg>`;
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const signed = (n) => (n < 0 ? `−${Math.abs(n)}` : String(n));
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export class ManualView {
  /** @param {{ socket, inspector, numpad, dealIntro, getHostId, onLeave, openSettings }} deps */
  constructor({ socket, inspector, numpad, dealIntro, getHostId, onLeave, openSettings }) {
    this._socket = socket;
    this._inspector = inspector;
    this._numpad = numpad;
    this._dealIntro = dealIntro;
    this._dealing = false;
    this._dealtUids = new Set();
    this._dealt = false;
    this._getHostId = getHostId;
    this._onLeave = onLeave;
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
         leave: () => this._onLeave() })[btn.dataset.act]?.();
    });
    document.addEventListener("keydown", (e) => this._onKey(e));
    document.getElementById("vm-settings-btn")?.addEventListener("click", () => this._openSettings());
    document.getElementById("vm-leave-btn")?.addEventListener("click", () => { if (window.confirm("Opustit hru?")) this._onLeave(); });
  }

  // ------------------------------------------------------------------- state

  _me() { return this._state.players.find(p => p.player_id === this._state.you); }
  _p(pid) { return this._state.players.find(p => p.player_id === pid); }
  _names() { return Object.fromEntries(this._state.players.map(p => [p.player_id, p.display_name])); }
  _isHost() { return this._getHostId() === this._state?.you; }

  _onState(state) {
    if (state.mode !== "vydrz_rucne") return;
    const prevPhase = this._state?.phase;
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
    const deal = state.phase === "planning" && !this._dealt && getSettings().deal;
    if (state.phase === "planning") this._dealt = true;
    if (deal) { this._dealing = true; this._dealtUids.clear(); }
    this.render();
    if (deal) requestAnimationFrame(() => this._runDeal());
    this._maybeRevealDrinks();
  }

  /** The 16 cards are shown pair by pair (see deal-intro.js), then land in the hand. */
  async _runDeal() {
    const me = this._me();
    const slotOf = (kind, uid) => this._el.stage.querySelector(`.vy-slot[data-uid="${CSS.escape(uid)}"]`);
    await this._dealIntro.play({
      pairs: me.locations.map((l, i) => ({ lokace: l, existence: me.existences[i] })),
      counts: { lokace: this._state.decks.lokace, existence: this._state.decks.existence },
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
      reveal: () => this._stageReveal(), finished: () => this._stageFinal(),
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
      if (s.phase === "planning") status = p.planned ? "✔ plán hotov" : "plánuje…";
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
      return pair
        ? `<li class="is-set"><span class="vm-n">${i + 1}</span><span class="vm-plan__loc">${esc(byUid[pair.location_uid].name)} <b>(${byUid[pair.location_uid].number})</b></span><span class="vm-plan__ex">${esc(byUid[pair.existence_uid].name)}</span></li>`
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
      <p class="vm-info">Rozděl si <b>všech ${this._state.rounds_total} dvojic</b> předem. Dvojice č. 1 se použije v 1. kole, č. 2 ve 2. a tak dál. Ostatní uvidí tvé karty, ne pořadí.</p>
      <section class="vm-plan"><h3>Tvůj plán</h3><ol class="vm-plan__list">${rows}</ol></section>
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
    if (reducedMotion()) { this._revealed[drawId] = c.drinks.length; this.render(); return; }
    this._revealing = true;
    this.render();
    for (let i = 1; i <= c.drinks.length; i++) {
      await new Promise(r => setTimeout(r, i === 1 ? 500 : 750));
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
    sfx.unlockAudio(); sfx.tick(delta, this._value);
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
      confirmLabel: "Použít ↵",
      onSubmit: (n) => { this._value = n; this._fresh = true; this._paintValue(); },   // sending is the Enter button's job
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
    } else if (k === "-" || k === "_") {
      if (this._value === 0) this._negPending = !this._negPending;     // "-" first, digits after
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

  _stageReveal() {
    const r = this._state.last_result;
    if (!r) return "";
    const names = this._names();
    const win = r.winner_id
      ? `<b>${esc(names[r.winner_id])}</b> bere lokaci <b>${esc(r.picks[r.winner_id].lokace.name)}</b> a získává <b class="vr__plus">+${r.awarded}</b> b.`
      : "Remíza se nepodařilo rozhodnout. Lokaci nikdo nezíská.";
    return `
      <h2 class="vm-banner">Kolo ${r.round}: výsledek</h2>
      <section class="vm-drinks"><h3>Vytažený chlast</h3><div class="vm-drinks__cards vm-drinks__cards--done">${r.drawn.map(d => `<div class="vm-drink">${drinkFront(d)}</div>`).join("")}</div></section>
      <section class="vm-table"><h3>Výsledky</h3>${this._resultTable(r)}${this._tiebreakNotes(r.tiebreak)}</section>
      <div class="vr__outcome">${win}</div>`;
  }

  _stageFinal() {
    const r = this._state.last_result;
    const f = r?.final;
    if (!f) return "";
    const names = this._names();
    const label = (pid) => (pid === f.winner_id ? "Vítěz" : pid === f.loser_id ? "Poražený" : "");
    return `
      <h2 class="vm-banner">Konec hry</h2>
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
    } else if (s.phase === "finished") {
      html = `<button class="btn btn--block" data-act="leave" type="button">Opustit hru</button>`;
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
        <p class="vs__note">${p.planned ? "Pořadí dvojic je skryté do vyhodnocení jednotlivých kol." : "Ještě plánuje."}</p>
        <h3>Postava</h3><div class="vs__row">${characterFront(p.character, { extra: "vc--mini" })}</div>
        <h3>Lokace (${p.locations.length})</h3><div class="vs__row">${p.locations.map(c => locationFront(c, { extra: "vc--mini" })).join("") || "<p class='vy-empty'>—</p>"}</div>
        <h3>Existence (${p.existences.length})</h3><div class="vs__row">${p.existences.map(c => existenceFront(c, { extra: "vc--mini" })).join("") || "<p class='vy-empty'>—</p>"}</div>
        ${p.won.length ? `<h3>Získané lokace</h3><div class="vs__row">${p.won.map(c => locationFront(c, { extra: "vc--mini" })).join("")}</div>` : ""}
      </div>`;
    sheet.hidden = false;
    sheet.onclick = (e) => { if (e.target.closest("[data-close]")) sheet.hidden = true; };
  }
}
