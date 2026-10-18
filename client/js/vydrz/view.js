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
  cardBack, cardFront, cardMeta, characterFront, existenceFront, locationFront, drinkFront,
} from "./cards.js";
import { ResultOverlay } from "./result-overlay.js";
import { getSettings, updateSettings } from "./settings.js";
import * as sfx from "./sound.js";

const LONG_PRESS_MS = 450;
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export class VydrzView {
  /**
   * @param {{ socket, inspector, getHostId: () => string|null, onLeave: () => void }} deps
   */
  constructor({ socket, inspector, getHostId, onLeave }) {
    this._socket = socket;
    this._inspector = inspector;
    this._onLeave = onLeave;
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
      piles: document.getElementById("vy-piles"),
      drawn: document.getElementById("vy-drawn"),
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
    });

    bus.on("server:vydrz_state", (s) => this._onState(s));
    this._wire();
  }

  // ------------------------------------------------------------------ input

  _wire() {
    const { locs, exs, players, bar, sheet, settings } = this._el;

    for (const [container, kind] of [[locs, "lokace"], [exs, "existence"]]) {
      container.addEventListener("click", (e) => {
        const inspectBtn = e.target.closest("[data-inspect]");
        const card = e.target.closest(".vy-slot")?.querySelector(".vc[data-uid]");
        if (!card) return;
        if (this._suppressClick) { this._suppressClick = false; return; }
        if (inspectBtn) { this._inspectCards(this._me()[kind === "lokace" ? "locations" : "existences"], card.dataset.uid); return; }
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
    this._el.piles.addEventListener("click", () => {});     // piles are decorative

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
      const card = e.target.closest(".vc[data-uid]");
      if (card && this._viewPlayer) {
        const p = this._player(this._viewPlayer);
        const all = [p.character, ...p.locations, ...p.existences, ...p.won];
        this._inspectCards(all, card.dataset.uid);
      }
    });

    document.getElementById("vy-settings-btn")?.addEventListener("click", () => this._openSettings());
    document.getElementById("vy-leave-btn")?.addEventListener("click", () => {
      if (window.confirm("Opustit hru?")) this._onLeave();
    });
    settings.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) { settings.hidden = true; return; }
      const sound = e.target.closest("[data-set-sound]");
      if (sound) { updateSettings({ sound: sound.dataset.setSound === "on" }); sfx.unlockAudio(); sfx.pop(); this._renderSettings(); }
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
    const prevRound = this._state?.round;
    this._state = state;
    this._showPanel();

    // New round: drop selections. Keep them while the same round is in progress.
    if (state.round !== prevRound) { this._sel = { lokace: null, existence: null }; this._changing = false; }
    const me = this._me();
    if (me?.pick && !this._changing) this._sel = { lokace: me.pick.location_uid, existence: me.pick.existence_uid };

    // The result of a freshly resolved round: animate it once, hold scores until it finishes.
    const result = state.last_result;
    const fresh = result && result.round > this._seenRound;
    if (fresh) this._holdScores = result.scores_before;

    const deal = state.round === 1 && state.phase === "choosing" && !this._dealt;
    this.render({ deal });
    if (deal) this._dealt = true;
    // The host started the next round: the old result is no longer relevant.
    if (state.phase === "choosing" && result && result.round < state.round && this._overlay.isOpen) this._overlay.close();
    if (fresh) this._playResult(result);
  }

  async _playResult(result) {
    this._seenRound = result.round;
    await this._overlay.open(result, this._resultCtx(), { animate: true });
    this._holdScores = null;
    this._renderPlayers();
  }

  _resultCtx() {
    return { names: this._names(), isHost: this.isHost(), myId: this._state.you };
  }

  _reopenResult() {
    const r = this._state?.last_result;
    if (!r) return;
    this._overlay.open(r, this._resultCtx(), { animate: false });
    this._holdScores = null;
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
    if (deal) requestAnimationFrame(() => this._dealAnimation());
    if (this._sheetOpen()) this._renderPlayerSheet();
  }

  _renderPlayers() {
    const s = this._state;
    const scores = this._holdScores;
    this._el.players.innerHTML = s.players.map((p) => `
      <button class="vy-player${p.player_id === s.you ? " is-me" : ""}${p.locked && s.phase === "choosing" ? " is-locked" : ""}" type="button" data-pid="${esc(p.player_id)}">
        <span class="vy-player__name">${esc(p.display_name)}</span>
        <span class="vy-player__score">${scores ? scores[p.player_id] : p.score}<small> b.</small></span>
        <span class="vy-player__state">${s.phase === "choosing" ? (p.locked ? "✔ vybráno" : "vybírá…") : (p.won.length ? `${p.won.length}× lokace` : "")}</span>
      </button>`).join("");
  }

  _renderTable() {
    const d = this._state.decks;
    this._el.piles.innerHTML = [
      ["postava", "Postavy", d.postava],
      ["chlast", "Chlast", d.chlast],
      ["lokace", "Lokace", d.lokace],
      ["existence", "Existence", d.existence],
    ].map(([kind, label, n]) => `
      <div class="vy-pile" data-pile="${kind}">
        <div class="vy-pile__stack">${cardBack(kind)}</div>
        <span class="vy-pile__label">${label} <b>${n}</b></span>
      </div>`).join("");

    const r = this._state.last_result;
    const drawn = r && this._state.phase !== "choosing" ? r.drawn.map(x => x.drink) : [];
    this._el.drawn.innerHTML = `<span class="vy-drawn__label">Vytažený chlast</span><div class="vy-drawn__cards">` +
      [0, 1, 2].map(i => drawn[i] ? drinkFront(drawn[i]) : cardBack("chlast")).join("") + `</div>`;
  }

  _renderMe() {
    const me = this._me();
    this._el.me.innerHTML = `<div class="vy-me__card">${characterFront(me.character)}</div>
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
      return `<div class="vy-slot${selected ? " is-selected" : ""}${locked ? " is-locked" : ""}">
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
    this._el.settings.innerHTML = `
      <div class="vs__backdrop" data-close></div>
      <div class="vs__panel vs__panel--small">
        <header class="vs__head"><h2>Nastavení</h2><button class="hud__btn" data-close type="button" aria-label="Zavřít">✕</button></header>
        <h3>Počítání bodů</h3>
        <div class="seg"><button class="seg__btn is-on" type="button" disabled>Automaticky</button>
          <button class="seg__btn" type="button" disabled title="Připravujeme">Ručně (brzy)</button></div>
        <h3>Zvuk</h3>
        <div class="seg"><button class="seg__btn${st.sound ? " is-on" : ""}" data-set-sound="on" type="button">Zapnutý</button>
          <button class="seg__btn${st.sound ? "" : " is-on"}" data-set-sound="off" type="button">Vypnutý</button></div>
      </div>`;
  }

  // ------------------------------------------------------------ deal effect

  /**
   * Cards fly from the piles to the hand. The piles are visible on the table
   * (see _renderTable), so this shows where the cards come from.
   */
  _dealAnimation() {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const groups = [
      ["postava", this._el.me.querySelectorAll(".vc")],
      ["lokace", this._el.locs.querySelectorAll(".vy-slot")],
      ["existence", this._el.exs.querySelectorAll(".vy-slot")],
    ];
    let n = 0;
    for (const [kind, nodes] of groups) {
      const pile = this._el.piles.querySelector(`[data-pile="${kind}"] .vc`);
      if (!pile) continue;
      const from = pile.getBoundingClientRect();
      nodes.forEach((node) => {
        const to = node.getBoundingClientRect();
        const dx = from.left + from.width / 2 - (to.left + to.width / 2);
        const dy = from.top + from.height / 2 - (to.top + to.height / 2);
        node.animate(
          [{ transform: `translate(${dx}px, ${dy}px) scale(.45) rotate(-8deg)`, opacity: 0 },
           { opacity: 1, offset: 0.15 },
           { transform: "none", opacity: 1 }],
          { duration: 520, delay: n++ * 55, easing: "cubic-bezier(.22,1,.36,1)", fill: "backwards" },
        );
      });
    }
  }
}
