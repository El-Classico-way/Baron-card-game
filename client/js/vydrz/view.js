/**
 * client/js/vydrz/view.js
 *
 * VydrzView — the Výdrž table. Renders the latest server snapshot
 * ("server:vydrz_state") and sends the player's intent back. It never
 * decides outcomes: picks are validated and scored by the server.
 *
 * UI-only state kept here: which two cards are selected (before confirming),
 * which player's cards are open in the side sheet, and the result overlay.
 */

import { bus } from "../core/events.js";
import {
  cardBack, cardFront, cardMeta, characterFront, existenceFront, locationFront, drinkFront, DRINKS,
} from "./cards.js";
import { pilesHtml, onPileClick } from "./piles.js";
import { ResultOverlay } from "./result-overlay.js";
import { getSettings, updateSettings, SPEED_LABELS } from "./settings.js";
import * as sfx from "./sound.js";

const LONG_PRESS_MS = 450;
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Host-only: remove a player who dropped out, so the game stops waiting for them. */
export function dropButton(view, p) {
  if (!view.isHost() || p.player_id === view._state.you || p.connected !== false || p.away) return "";
  return `<div class="vs__drop"><p class="vs__note">${esc(p.display_name)} je odpojen(a).</p>
    <button class="btn btn--small" type="button" data-drop="${esc(p.player_id)}">Hrát bez něj/ní</button></div>`;
}

export class VydrzView {
  /**
   * @param {{ socket, inspector, dealIntro, getHostId: () => string|null, onLeave: () => void, onConfirmLeave?: () => void }} deps
   */
  constructor({ socket, inspector, dealIntro, getHostId, onLeave, onConfirmLeave }) {
    this._socket = socket;
    this._inspector = inspector;
    this._dealIntro = dealIntro;
    this._dealing = false;                             // opening deal in progress
    this._dealtUids = new Set();                       // cards that have landed in the hand
    this._charLanded = true;                           // false while the character card is being assigned
    this._onLeave = onLeave;
    this._confirmLeave = onConfirmLeave ?? (() => onLeave());
    this._getHostId = getHostId;
    this._changing = false;                            // player reopened their confirmed pick
    this._state = null;
    this._sel = { lokace: null, existence: null };     // uids chosen but maybe not yet sent
    this._seenRound = 0;                               // last round whose animation has played
    this._holdScores = null;                           // scores shown while a result is animating
    this._dealt = false;                               // deal animation already ran
    this._viewPlayer = null;

    this._el = {
      panel: document.getElementById("vydrz-panel"),
      round: document.getElementById("vy-round"),
      players: document.getElementById("vy-players"),
      table: document.getElementById("vy-table"),
      me: document.getElementById("vy-me"),
      locs: document.getElementById("vy-locations"),
      exs: document.getElementById("vy-existences"),
      bar: document.getElementById("vy-bar"),
      sheet: document.getElementById("vy-sheet"),
      settings: document.getElementById("vy-settings"),
      result: document.getElementById("vy-result"),
    };
    this._overlay = new ResultOverlay(this._el.result, {
      onNext: () => this._socket.send("vydrz_next_round"),
      onLeave: () => this._onLeave(),
      onInspect: (cards, uid) => this._inspectCards(cards, uid),
      onAnnounce: () => this._socket.send("vydrz_announce"),
      onLobby: () => this._socket.send("return_to_lobby"),
    });

    bus.on("server:vydrz_state", (s) => this._onState(s));
    this._wire();
  }

  // ------------------------------------------------------------------ input

  _wire() {
    const { locs, exs, players, bar, sheet, settings } = this._el;

    onPileClick(this._el.table, () => this._state, (cards, uid) => this._inspectCards(cards, uid));

    for (const [container, kind] of [[locs, "lokace"], [exs, "existence"]]) {
      container.addEventListener("click", (e) => {
        const inspectBtn = e.target.closest("[data-inspect]");
        const card = e.target.closest(".vy-slot")?.querySelector(".vc[data-uid]");
        if (!card) return;
        if (this._suppressClick) { this._suppressClick = false; return; }
        if (inspectBtn) { this._inspectCards(this._me()[kind === "lokace" ? "locations" : "existences"], card.dataset.uid); return; }
        // Nothing to choose any more (confirmed pick, or the round is over): a tap just looks at the card.
        if (this._state.phase !== "choosing" || this._locked()) { this._inspectCards(this._me()[kind === "lokace" ? "locations" : "existences"], card.dataset.uid); return; }
        // A second tap on the same card within a moment looks at it (the cards are re-rendered between taps, so no dblclick event).
        const now = performance.now();
        if (this._lastTap?.uid === card.dataset.uid && now - this._lastTap.t < 450) {
          this._lastTap = null;
          this._inspectCards(this._me()[kind === "lokace" ? "locations" : "existences"], card.dataset.uid);
          return;
        }
        this._lastTap = { uid: card.dataset.uid, t: now };
        this._select(kind, card.dataset.uid);
      });
      this._longPress(container, ".vc[data-uid]", (uid) =>
        this._inspectCards(this._me()[kind === "lokace" ? "locations" : "existences"], uid));
      container.addEventListener("contextmenu", (e) => {
        const card = e.target.closest(".vc[data-uid]");
        if (!card) return;
        e.preventDefault();
        this._inspectCards(this._me()[kind === "lokace" ? "locations" : "existences"], card.dataset.uid);
      });
    }

    players.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-pid]");
      if (chip) this._openPlayerSheet(chip.dataset.pid);
    });
    this._el.me.addEventListener("click", () => this._inspectCards([this._me().character], this._me().character.uid));

    bar.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      sfx.unlockAudio();
      if (btn.dataset.act === "confirm") this._confirm();
      if (btn.dataset.act === "change") this._change();
      if (btn.dataset.act === "result") this._reopenResult();
    });

    sheet.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) { this._closeSheet(); return; }
      const drop = e.target.closest("[data-drop]");
      if (drop) { this._socket.send("drop_player", { player_id: drop.dataset.drop }); this._closeSheet(); return; }
      const card = e.target.closest(".vc[data-uid]");
      if (card && this._viewPlayer) {
        const p = this._player(this._viewPlayer);
        const all = [p.character, ...p.locations, ...p.existences, ...p.won];
        this._inspectCards(all, card.dataset.uid);
      }
    });

    document.getElementById("vy-settings-btn")?.addEventListener("click", () => this._openSettings());
    document.getElementById("vy-leave-btn")?.addEventListener("click", () => this._confirmLeave());
    settings.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) { settings.hidden = true; return; }
      const sound = e.target.closest("[data-set-sound]");
      if (sound) { updateSettings({ sound: sound.dataset.setSound === "on" }); sfx.unlockAudio(); sfx.pop(); this._renderSettings(); }
      const deal = e.target.closest("[data-set-deal]");
      if (deal) { updateSettings({ deal: deal.dataset.setDeal === "on" }); sfx.pop(); this._renderSettings(); }
      const speed = e.target.closest("[data-set-speed]");
      if (speed) { updateSettings({ speed: parseInt(speed.dataset.setSpeed, 10) }); sfx.unlockAudio(); sfx.tick(1, 0); this._renderSettings(); }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!settings.hidden) settings.hidden = true;
      else if (!sheet.hidden) this._closeSheet();
    });
  }

  _longPress(container, selector, handler) {
    let timer = null, sx = 0, sy = 0, uid = null;
    const cancel = () => { clearTimeout(timer); timer = null; };
    container.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const card = e.target.closest(selector);
      if (!card) return;
      uid = card.dataset.uid; sx = e.clientX; sy = e.clientY;
      timer = setTimeout(() => { cancel(); this._suppressClick = true; handler(uid); }, LONG_PRESS_MS);
    });
    container.addEventListener("pointermove", (e) => {
      if (timer && Math.hypot(e.clientX - sx, e.clientY - sy) > 10) cancel();
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(t => container.addEventListener(t, cancel));
    container.addEventListener("scroll", cancel, { passive: true });
  }

  // ------------------------------------------------------------------ state

  _me() { return this._player(this._state.you); }
  _player(pid) { return this._state.players.find(p => p.player_id === pid); }
  _names() { return Object.fromEntries(this._state.players.map(p => [p.player_id, p.display_name])); }
  isHost() { return this._getHostId() === this._state?.you; }
  _locked() { return !!this._me()?.locked && !this._changing; }

  _onState(state) {
    if (state.mode !== "vydrz") return;           // the manual variant has its own view
    const first = !this._state;                   // first state seen since the page loaded or the game began
    const prevRound = this._state?.round;
    const wasAnnounced = this._state?.winner_announced ?? false;
    this._state = state;
    this._showPanel();

    // New round: drop selections. Keep them while the same round is in progress.
    if (state.round !== prevRound) { this._sel = { lokace: null, existence: null }; this._changing = false; }
    const me = this._me();
    if (me?.pick && !this._changing) this._sel = { lokace: me.pick.location_uid, existence: me.pick.existence_uid };

    // The result of a freshly resolved round: animate it once, hold scores until it finishes.
    const result = state.last_result;
    // A state that is just being (re)loaded shows where the game stands, without replaying anything.
    if (first && result) this._seenRound = result.round;
    const fresh = result && result.round > this._seenRound;
    if (fresh) this._holdScores = result.scores_before;

    const deal = state.round === 1 && state.phase === "choosing" && !this._dealt && getSettings().deal;
    if (deal) { this._dealing = true; this._charLanded = false; this._dealtUids.clear(); }      // hide the cards until they land
    this.render({ deal });
    if (state.round === 1 && state.phase === "choosing") this._dealt = true;
    // The host started the next round: the old result is no longer relevant.
    if (state.phase === "choosing" && result && result.round < state.round && this._overlay.isOpen) this._overlay.close();
    if (fresh) this._playResult(result);
    else if (first && result && state.phase !== "choosing") this._reopenResult();     // back where they were
    else if (state.winner_announced && !wasAnnounced && !first) this._playAnnouncement();
  }

  /** Host announced the winner while we are watching: splash once, then the final ranking. */
  async _playAnnouncement() {
    const r = this._state.last_result;
    if (!r?.final) return;
    if (!this._overlay.isOpen) await this._overlay.open(r, this._resultCtx(), { animate: false });
    await this._overlay.announce(r.final, this._resultCtx(), { animate: true });
  }

  /** Back to the pre-game lobby: forget everything about the finished game. */
  reset() {
    this._overlay.close();
    this._state = null;
    this._seenRound = 0;
    this._holdScores = null;
    this._dealt = false;
    this._dealing = false;
    this._charLanded = false;
    this._dealtUids.clear();
    this._sel = { lokace: null, existence: null };
    this._closeSheet();
    this._el.settings.hidden = true;
    this._el.panel.hidden = true;
  }

  async _playResult(result) {
    this._seenRound = result.round;
    await this._overlay.open(result, this._resultCtx(), { animate: true });
    this._holdScores = null;
    this._renderPlayers();
  }

  _resultCtx() {
    return { names: this._names(), isHost: this.isHost(), myId: this._state.you, character: this._me()?.character };
  }

  _reopenResult() {
    const r = this._state?.last_result;
    if (!r) return;
    this._holdScores = null;
    this._overlay.open(r, this._resultCtx(), { animate: false });
  }

  _showPanel() {
    ["join-panel", "lobby-panel", "game-panel"].forEach(id => { const el = document.getElementById(id); if (el) el.hidden = true; });
    this._el.panel.hidden = false;
  }

  // ----------------------------------------------------------------- render

  render({ deal = false } = {}) {
    const s = this._state;
    if (!s) return;
    this._el.round.textContent = s.phase === "finished" ? "Konec hry" : `Kolo ${s.round} / ${s.rounds_total}`;
    this._renderPlayers();
    this._renderTable();
    this._renderMe();
    this._renderHands();
    this._renderBar();
    if (deal) requestAnimationFrame(() => this._runDeal());
    if (this._sheetOpen()) this._renderPlayerSheet();
  }

  _renderPlayers() {
    const s = this._state;
    const scores = this._holdScores;
    this._el.players.innerHTML = s.players.map((p) => `
      <button class="vy-player${p.player_id === s.you ? " is-me" : ""}${p.locked && s.phase === "choosing" ? " is-locked" : ""}" type="button" data-pid="${esc(p.player_id)}">
        <span class="vy-player__name">${esc(p.display_name)}</span>
        <span class="vy-player__score">${scores ? scores[p.player_id] : p.score}<small> b.</small></span>
        <span class="vy-player__state">${p.away ? "odešel(a)" : p.connected === false ? "odpojen(a)" : s.phase === "choosing" ? (p.locked ? "✔ vybráno" : "vybírá…") : (p.won.length ? `${p.won.length}× lokace` : "")}</span>
      </button>`).join("");
  }

  _renderTable() {
    this._el.table.innerHTML = pilesHtml(this._state);
  }

  _renderMe() {
    const me = this._me();
    this._el.me.innerHTML = `<div class="vy-me__card${this._charLanded ? "" : " is-undealt"}" data-char>${characterFront(me.character)}</div>
      <div class="vy-me__info">
        <h3>${esc(me.display_name)}</h3>
        <p>Tvoje postava. Klepni pro zvětšení.</p>
        ${me.won.length ? `<p class="vy-me__won">Získané lokace: ${me.won.map(l => `<b>${l.number}</b>`).join(" ")}</p>` : ""}
      </div>`;
  }

  _renderHands() {
    const me = this._me();
    const locked = this._state.phase !== "choosing" || this._locked();
    const draw = (cards, kind, front) => cards.map((c) => {
      const selected = this._sel[kind] === c.uid;
      const undealt = this._dealing && !this._dealtUids.has(c.uid);
      return `<div class="vy-slot${selected ? " is-selected" : ""}${locked ? " is-locked" : ""}${undealt ? " is-undealt" : ""}" data-uid="${esc(c.uid)}">
        ${front(c, { extra: "vc--hand", attrs: `data-selected="${selected}"` })}
        ${selected ? `<button class="vy-inspect" type="button" data-inspect aria-label="Zvětšit kartu">🔍</button>` : ""}
      </div>`;
    }).join("") || `<p class="vy-empty">Žádné karty.</p>`;
    this._el.locs.innerHTML = draw(me.locations, "lokace", locationFront);
    this._el.exs.innerHTML = draw(me.existences, "existence", existenceFront);
  }

  _renderBar() {
    const s = this._state, me = this._me();
    const picked = this._sel.lokace && this._sel.existence;
    const locked = s.players.filter(p => p.locked).length;
    let html = "";
    if (s.phase === "choosing") {
      const slot = (kind, label) => {
        const card = (kind === "lokace" ? me.locations : me.existences).find(c => c.uid === this._sel[kind]);
        return `<div class="vy-pick"><small>${label}</small><b>${card ? esc(card.name) : "—"}</b></div>`;
      };
      html = `<div class="vy-bar__picks">${slot("lokace", "Lokace")}${slot("existence", "Existence")}</div>`;
      if (this._locked()) {
        html += `<div class="vy-bar__row"><span class="vy-wait">Čeká se na ostatní (${locked}/${s.players.length})</span>
                 <button class="btn btn--ghost btn--small" data-act="change" type="button">Změnit výběr</button></div>`;
      } else {
        html += `<button class="btn btn--block" data-act="confirm" type="button" ${picked ? "" : "disabled"}>${picked ? "Potvrdit výběr" : "Vyber lokaci i existenci"}</button>`;
      }
    } else if (s.phase === "reveal" || s.phase === "finished") {
      html = `<button class="btn btn--block" data-act="result" type="button">Zobrazit výsledek kola</button>`;
    }
    this._el.bar.innerHTML = html;
  }

  // ---------------------------------------------------------------- actions

  _select(kind, uid) {
    if (this._state.phase !== "choosing") return;
    const me = this._me();
    if (this._locked()) return;                  // press "Změnit výběr" first
    this._sel[kind] = this._sel[kind] === uid ? null : uid;
    sfx.unlockAudio(); sfx.pop();
    this._renderHands();
    this._renderBar();
  }

  _confirm() {
    if (!(this._sel.lokace && this._sel.existence)) return;
    this._changing = false;
    this._socket.send("vydrz_choose", { location_uid: this._sel.lokace, existence_uid: this._sel.existence });
  }

  _change() {
    // The server keeps the old pick until a new one arrives; locally we just
    // reopen the selection. Confirming again replaces the pick.
    this._changing = true;
    this._renderHands();
    this._renderBar();
  }

  _inspectCards(cards, uid) {
    const items = cards.map((c) => ({
      uid: c.uid,
      html: cardFront(c, { extra: "vc--big" }),
      name: c.name,
      meta: cardMeta(c),
      desc: c.description ?? "",
    }));
    const index = Math.max(0, items.findIndex(i => i.uid === uid));
    this._inspector.openCustom(items, index);
  }

  // --------------------------------------------------------- other players

  _sheetOpen() { return !this._el.sheet.hidden; }

  _openPlayerSheet(pid) {
    this._viewPlayer = pid;
    this._el.sheet.hidden = false;
    this._renderPlayerSheet();
  }

  _closeSheet() { this._el.sheet.hidden = true; this._viewPlayer = null; }

  _renderPlayerSheet() {
    const p = this._player(this._viewPlayer);
    if (!p) { this._closeSheet(); return; }
    const pickInfo = p.pick
      ? `<p class="vs__note">Vybráno: ${esc((p.locations.find(l => l.uid === p.pick.location_uid) ?? {}).name)} + ${esc((p.existences.find(e => e.uid === p.pick.existence_uid) ?? {}).name)}</p>`
      : (p.locked ? `<p class="vs__note">Už si vybral(a), výběr je skrytý do vyhodnocení.</p>` : "");
    this._el.sheet.innerHTML = `
      <div class="vs__backdrop" data-close></div>
      <div class="vs__panel">
        <header class="vs__head"><h2>${esc(p.display_name)} <small>${p.score} b.</small></h2>
          <button class="hud__btn" data-close type="button" aria-label="Zavřít">✕</button></header>
        ${dropButton(this, p)}
        ${pickInfo}
        <h3>Postava</h3><div class="vs__row">${characterFront(p.character, { extra: "vc--mini" })}</div>
        <h3>Lokace (${p.locations.length})</h3><div class="vs__row">${p.locations.map(c => locationFront(c, { extra: "vc--mini" })).join("") || "<p class='vy-empty'>—</p>"}</div>
        <h3>Existence (${p.existences.length})</h3><div class="vs__row">${p.existences.map(c => existenceFront(c, { extra: "vc--mini" })).join("") || "<p class='vy-empty'>—</p>"}</div>
        ${p.won.length ? `<h3>Získané lokace</h3><div class="vs__row">${p.won.map(c => locationFront(c, { extra: "vc--mini" })).join("")}</div>` : ""}
      </div>`;
  }

  // --------------------------------------------------------------- settings

  _openSettings() {
    this._renderSettings();
    this._el.settings.hidden = false;
  }

  _renderSettings() {
    const st = getSettings();
    const speeds = SPEED_LABELS.map((label, i) =>
      `<button class="seg__btn${st.speed === i + 1 ? " is-on" : ""}" data-set-speed="${i + 1}" type="button">${label}</button>`).join("");
    this._el.settings.innerHTML = `
      <div class="vs__backdrop" data-close></div>
      <div class="vs__panel vs__panel--small">
        <header class="vs__head"><h2>Nastavení</h2><button class="hud__btn" data-close type="button" aria-label="Zavřít">✕</button></header>
        <h3>Rychlost automatického počítání</h3>
        <div class="seg seg--wrap">${speeds}</div>
        <p class="vs__note vs__note--dim">Platí jen pro režim Výdrž (automaticky). Nastavení je jen na tomto zařízení.</p>
        <h3>Zvuk</h3>
        <div class="seg"><button class="seg__btn${st.sound ? " is-on" : ""}" data-set-sound="on" type="button">Zapnutý</button>
          <button class="seg__btn${st.sound ? "" : " is-on"}" data-set-sound="off" type="button">Vypnutý</button></div>
        <h3>Animace rozdávání karet</h3>
        <div class="seg"><button class="seg__btn${st.deal ? " is-on" : ""}" data-set-deal="on" type="button">Zapnutá</button>
          <button class="seg__btn${st.deal ? "" : " is-on"}" data-set-deal="off" type="button">Vypnutá</button></div>
      </div>`;
  }

  // ------------------------------------------------------------ opening deal

  /** The 16 cards are shown pair by pair (see deal-intro.js), then land in the hand. */
  async _runDeal() {
    const me = this._me();
    const slotOf = (kind, uid) => this._el[kind === "lokace" ? "locs" : "exs"].querySelector(`.vy-slot[data-uid="${CSS.escape(uid)}"]`);
    await this._dealIntro.play({
      character: me.character,
      getCharSlot: () => this._el.me.querySelector(".vy-me__card"),
      onLandChar: () => {
        this._charLanded = true;
        const el = this._el.me.querySelector(".vy-me__card");
        if (el) { el.classList.remove("is-undealt"); el.classList.add("is-dealt-pop"); }
      },
      pairs: me.locations.map((l, i) => ({ lokace: l, existence: me.existences[i] })),
      counts: { postava: this._state.decks.postava, lokace: this._state.decks.lokace, existence: this._state.decks.existence },
      getSlot: slotOf,
      onLand: (uid) => {
        this._dealtUids.add(uid);
        for (const kind of ["lokace", "existence"]) {
          const slot = slotOf(kind, uid);
          if (slot) { slot.classList.remove("is-undealt"); slot.classList.add("is-dealt-pop"); }
        }
      },
      prepare: () => {
        const scroller = this._el.locs.closest(".vy-scroll");
        const section = this._el.locs.closest("section");
        if (scroller && section) scroller.scrollTop = section.offsetTop - 8;
      },
    });
    this._dealing = false;
    this._charLanded = true;
    this._renderMe();
    this._renderHands();
  }
}
