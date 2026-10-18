/**
 * client/js/vydrz/numpad.js
 *
 * On-screen number pad used to enter the counted number (Výdrž ručně). It
 * replaces the phone's keyboard: the field that opens it is a button, not a
 * text input, so no system keyboard appears.
 *
 * Keys: 0-9, ± (sign), ⌫ and a large Enter button. On a computer the physical
 * keyboard works too: digits, "-" (or "+") for the sign, Backspace, Enter to
 * confirm and Escape to close.
 */

import * as sfx from "./sound.js";

const MAX_DIGITS = 3;

export class Numpad {
  /** @param {HTMLElement} root the (hidden) container */
  constructor(root) {
    this._root = root;
    this._digits = "";        // magnitude as typed, e.g. "12"
    this._neg = false;
    this._submit = null;
    this._onKey = this._onKey.bind(this);
    this.isOpen = false;

    root.addEventListener("click", (e) => {
      const key = e.target.closest("[data-key]");
      if (key) { this._press(key.dataset.key); return; }
      if (e.target.closest("[data-close]")) this.close();
    });
  }

  /**
   * @param {{ title: string, hint?: string, initial?: number|null, confirmLabel?: string, onSubmit: (n:number)=>void }} opts
   */
  open({ title, hint = "", initial = null, confirmLabel = "Potvrdit ↵", onSubmit }) {
    this._submit = onSubmit;
    this._neg = initial != null && initial < 0;
    this._digits = initial != null ? String(Math.abs(initial)) : "";
    this._root.innerHTML = `
      <div class="np__backdrop" data-close></div>
      <div class="np__panel" role="document">
        <header class="np__head">
          <div><h2 id="np-title">${esc(title)}</h2>${hint ? `<p class="np__hint">${esc(hint)}</p>` : ""}</div>
          <button class="hud__btn" type="button" data-close aria-label="Zavřít klávesnici">✕</button>
        </header>
        <div class="np__display" id="np-display" aria-live="polite"></div>
        <div class="np__keys">
          ${["1", "2", "3", "4", "5", "6", "7", "8", "9"].map(k => `<button type="button" class="np__key" data-key="${k}">${k}</button>`).join("")}
          <button type="button" class="np__key np__key--aux" data-key="sign" aria-label="Změnit znaménko">±</button>
          <button type="button" class="np__key" data-key="0">0</button>
          <button type="button" class="np__key np__key--aux" data-key="back" aria-label="Smazat">⌫</button>
          <button type="button" class="np__key np__key--enter" data-key="enter">${esc(confirmLabel)}</button>
        </div>
      </div>`;
    this._root.setAttribute("aria-labelledby", "np-title");
    this._root.hidden = false;
    this.isOpen = true;
    this._render();
    document.addEventListener("keydown", this._onKey, true);
  }

  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    this._root.hidden = true;
    this._root.innerHTML = "";
    document.removeEventListener("keydown", this._onKey, true);
  }

  /** Current value, or null if nothing was typed yet. */
  get value() {
    if (this._digits === "") return null;
    const n = parseInt(this._digits, 10);
    return this._neg ? -n : n;
  }

  _render() {
    const d = this._root.querySelector("#np-display");
    if (!d) return;
    const v = this.value;
    d.textContent = v === null ? (this._neg ? "−" : "–") : (this._neg ? "−" : "") + this._digits;
    d.classList.toggle("is-empty", v === null);
  }

  _press(key) {
    sfx.unlockAudio();
    if (/^[0-9]$/.test(key)) {
      if (this._digits === "0") this._digits = "";                // no leading zeros
      if (this._digits.length < MAX_DIGITS) this._digits += key;
      sfx.pop();
    } else if (key === "sign") {
      this._neg = !this._neg; sfx.pop();
    } else if (key === "back") {
      if (this._digits) this._digits = this._digits.slice(0, -1);
      else this._neg = false;
      sfx.pop();
    } else if (key === "enter") {
      const v = this.value;
      if (v === null) return;                                      // nothing typed
      const submit = this._submit;
      this.close();
      submit?.(v);
      return;
    }
    this._render();
  }

  _onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); this.close(); return; }
    if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); this._press("enter"); return; }
    if (e.key === "Backspace") { e.preventDefault(); this._press("back"); return; }
    if (e.key === "-" || e.key === "+" || e.key === "±") { e.preventDefault(); this._press("sign"); return; }
    if (/^[0-9]$/.test(e.key)) { e.preventDefault(); this._press(e.key); }
  }
}

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
