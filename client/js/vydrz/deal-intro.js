/**
 * client/js/vydrz/deal-intro.js
 *
 * The opening deal, shared by both Výdrž variants. First the player's character
 * is assigned (it flies from the character deck to the middle, turns over and
 * lands in its place), then the 16 cards are revealed pair by pair (one location + one existence): a pair flies from the
 * decks to the middle of the screen, turns face up so it can be read, then
 * flies into the hand. The next pair only starts when the previous one has
 * landed, so a new pair is always the thing on screen.
 *
 * Presentation only. The hand is already in the game state; the views keep
 * the not-yet-dealt cards invisible (see `isDealt`) until they land.
 */

import { cardBack, cardFront } from "./cards.js";
import * as sfx from "./sound.js";

const T = { fly: 340, flip: 240, hold: 400, land: 320, gap: 90 };

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

export class DealIntro {
  /** @param {HTMLElement} root the (hidden) overlay container */
  constructor(root) {
    this._root = root;
    this._token = 0;
    this._skip = false;
    this.isOpen = false;
  }

  /**
   * @param {{
   *   character?: object,
   *   getCharSlot?: () => HTMLElement|null,
   *   onLandChar?: () => void,
   *   pairs: { lokace: object, existence: object }[],
   *   counts: { postava?: number, lokace: number, existence: number },
   *   getSlot: (kind: "lokace"|"existence", uid: string) => HTMLElement|null,
   *   onLand: (uid: string) => void,
   *   prepare?: () => void,
   * }} opts
   * @returns {Promise<void>} resolves when everything has landed (or was skipped)
   */
  async play({ character, getCharSlot, onLandChar, pairs, counts, getSlot, onLand, prepare }) {
    const token = ++this._token;
    this._skip = !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const landAll = () => { onLandChar?.(); pairs.forEach(p => { onLand(p.lokace.uid); onLand(p.existence.uid); }); };
    if (this._skip) { landAll(); return; }

    prepare?.();                                  // e.g. scroll the hands into view
    this.isOpen = true;
    this._build(pairs.length, counts);
    this._root.hidden = false;
    const alive = () => token === this._token && !this._skip;

    await sleep(150);
    if (character && alive()) {
      this._q(".di__count").textContent = "Tvoje postava";
      await this._dealCharacter(character, getCharSlot, onLandChar, alive);
    }
    for (let i = 0; i < pairs.length && alive(); i++) {
      this._q(".di__count").textContent = `Dvojice ${i + 1} / ${pairs.length}`;
      await this._dealPair(pairs[i], getSlot, onLand, alive);
    }
    // Anything not landed yet (skip pressed) appears at once.
    landAll();
    if (token === this._token) this.close();
  }

  close() {
    this._token++;
    this.isOpen = false;
    this._root.hidden = true;
    this._root.innerHTML = "";
  }

  // ---------------------------------------------------------------- internals

  _q(sel) { return this._root.querySelector(sel); }

  _build(total, counts) {
    this._root.innerHTML = `
      <div class="di__backdrop"></div>
      <div class="di__panel" role="document">
        <header class="di__head">
          <h2 id="di-title">Rozdávání karet</h2>
          <span class="di__count">Dvojice 1 / ${total}</span>
          <button class="btn btn--ghost btn--small di__skip" type="button">Přeskočit</button>
        </header>
        <div class="di__piles">
          ${[["postava", "Postavy"], ["lokace", "Lokace"], ["existence", "Existence"]].map(([k, label]) => `<div class="di__pile" data-pile="${k}">
            <div class="di__pilecard">${cardBack(k)}</div><span>${label}${counts[k] != null ? ` <b>${counts[k]}</b>` : ""}</span></div>`).join("")}
        </div>
        <div class="di__stage"><div class="di__spot" data-spot="lokace"></div><div class="di__spot" data-spot="existence"></div></div>
        <p class="di__caption" aria-live="polite"></p>
      </div>`;
    this._root.setAttribute("aria-labelledby", "di-title");
    const skip = () => { this._skip = true; };
    this._q(".di__skip").addEventListener("click", skip);
    this._q(".di__backdrop").addEventListener("click", skip);
  }

  _make(kind, card, w) {
    const el = document.createElement("div");
    el.className = "di__card";
    el.style.setProperty("--di-w", `${w}px`);
    el.innerHTML = `<div class="di__inner"><div class="di__face di__face--back">${cardBack(kind)}</div>
      <div class="di__face di__face--front">${cardFront(card)}</div></div>`;
    this._root.appendChild(el);
    return el;
  }

  /** Assign the player's character: deck -> middle -> turn over -> into its place. */
  async _dealCharacter(card, getCharSlot, onLandChar, alive) {
    const stage = this._q(".di__stage").getBoundingClientRect();
    const w = Math.min(window.innerWidth * 0.5, 190), h = w * 1.4;
    const pile = this._q('[data-pile="postava"] .vc').getBoundingClientRect();
    const cx = stage.left + stage.width / 2, cy = stage.top + stage.height / 2;
    const el = this._make("postava", card, w);
    Object.assign(el.style, { left: `${cx - w / 2}px`, top: `${cy - h / 2}px`, width: `${w}px`, height: `${h}px` });
    this._q(".di__caption").textContent = card.name;

    sfx.pop();
    await el.animate([{ transform: `translate(${pile.left + pile.width / 2 - cx}px, ${pile.top + pile.height / 2 - cy}px) scale(${pile.width / w})`, opacity: 0.4 }, { transform: "none", opacity: 1 }],
      { duration: T.fly + 80, easing: "cubic-bezier(.22,1,.36,1)", fill: "both" }).finished;
    if (!alive()) { el.remove(); return; }
    sfx.flip();
    el.classList.add("is-flipped");
    await sleep(T.flip + 900);                                   // time to read the stats
    if (!alive()) { el.remove(); return; }

    const slot = getCharSlot?.();
    if (slot) {
      const to = slot.getBoundingClientRect();
      await el.animate([{ transform: "none" }, { transform: `translate(${to.left + to.width / 2 - cx}px, ${to.top + to.height / 2 - cy}px) scale(${to.width / w})` }],
        { duration: T.land + 60, easing: "cubic-bezier(.5,0,.75,0)", fill: "forwards" }).finished;
    }
    onLandChar?.();
    el.remove();
    this._q(".di__caption").textContent = "";
    sfx.pop();
    await sleep(T.gap);
  }

  async _dealPair(pair, getSlot, onLand, alive) {
    const w = Math.min(window.innerWidth * 0.36, 150);
    const sides = [["lokace", pair.lokace], ["existence", pair.existence]];
    const items = [];

    for (const [kind, card] of sides) {
      const spot = this._q(`[data-spot="${kind}"]`).getBoundingClientRect();
      const pile = this._q(`[data-pile="${kind}"] .vc`).getBoundingClientRect();
      const h = w * 1.4;
      const el = this._make(kind, card, w);
      Object.assign(el.style, { left: `${spot.left + spot.width / 2 - w / 2}px`, top: `${spot.top + spot.height / 2 - h / 2}px`, width: `${w}px`, height: `${h}px` });
      items.push({ kind, card, el, w, h, spot, pile });
    }

    // 1. fly from the decks to the middle
    sfx.pop();
    await Promise.all(items.map((it, i) => {
      const dx = it.pile.left + it.pile.width / 2 - (it.spot.left + it.spot.width / 2);
      const dy = it.pile.top + it.pile.height / 2 - (it.spot.top + it.spot.height / 2);
      const s = it.pile.width / it.w;
      return it.el.animate([{ transform: `translate(${dx}px, ${dy}px) scale(${s})`, opacity: 0.4 }, { transform: "none", opacity: 1 }],
        { duration: T.fly, delay: i * 70, easing: "cubic-bezier(.22,1,.36,1)", fill: "both" }).finished;
    }));
    if (!alive()) { items.forEach(it => it.el.remove()); return; }

    // 2. turn face up
    sfx.flip();
    items.forEach(it => it.el.classList.add("is-flipped"));
    await sleep(T.flip + T.hold);
    if (!alive()) { items.forEach(it => it.el.remove()); return; }

    // 3. fly into the hand
    await Promise.all(items.map((it) => {
      const slot = getSlot(it.kind, it.card.uid);
      if (!slot) return Promise.resolve();
      const row = slot.parentElement;
      if (row && row.scrollWidth > row.clientWidth) {
        row.scrollLeft = Math.max(0, slot.offsetLeft - (row.clientWidth - slot.offsetWidth) / 2);
      }
      const to = slot.getBoundingClientRect();
      const dx = to.left + to.width / 2 - (it.spot.left + it.spot.width / 2);
      const dy = to.top + to.height / 2 - (it.spot.top + it.spot.height / 2);
      const s = to.width / it.w;
      return it.el.animate([{ transform: "none" }, { transform: `translate(${dx}px, ${dy}px) scale(${s})` }],
        { duration: T.land, easing: "cubic-bezier(.5,0,.75,0)", fill: "forwards" }).finished.then(() => onLand(it.card.uid));
    }));
    items.forEach(it => it.el.remove());
    sfx.pop();
    await sleep(T.gap);
  }
}
