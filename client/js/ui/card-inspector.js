/**
 * client/js/ui/card-inspector.js
 *
 * CardInspector — shows one card enlarged, with its text, so it can be read.
 * Purely presentational: it reads cards from the store and never sends
 * anything to the server.
 *
 * Ways to open it
 *   - Long-press (touch) or right-click a card in your hand.
 *   - "Inspect" button in the action bar while a card is selected.
 *   - Tap a hand card when it is not your play/discard phase (nothing else
 *     could happen on a tap then, so a tap just inspects).
 *   - Tap the discard pile to inspect its top card.
 *
 * Closing: backdrop tap, close button, Escape.  Prev / Next (or arrow keys)
 * step through the hand.
 */

import { bus } from "../core/events.js";
import { CardView, SUIT_LABELS } from "./card-view.js";

const LONG_PRESS_MS  = 450;
const MOVE_TOLERANCE = 10;   // px a finger may drift before it counts as a scroll

export class CardInspector {
  /**
   * @param {Store} store
   * @param {{ handArea, discardTop, actionBar }} els
   */
  constructor(store, els) {
    this._store = store;
    this._els   = els;
    this._root  = document.getElementById("card-inspector");
    this._open  = false;
    this._source = null;         // { kind: "hand", uid } | { kind: "discard" }
    this._returnFocus = null;
    this._suppressClick = false;

    if (!this._root) return;
    this._wireHand();
    this._wireDiscard();
    this._wireActionButton();
    this._wireRoot();

    // Keep the view honest if the server changes things while it is open.
    bus.on("store:hand_updated",  () => this._refresh());
    bus.on("store:state_replaced", () => {
      this._refresh();
      this._syncInspectButton(this._store.state.ui.selectedCardUid);   // snapshot clears selection silently
    });
    bus.on("store:selection_changed", ({ cardUid }) => this._syncInspectButton(cardUid));
  }

  // ------------------------------------------------------------------
  // Open / close
  // ------------------------------------------------------------------

  openHandCard(uid) {
    this._source = { kind: "hand", uid };
    this._show();
  }

  openDiscardTop() {
    if (!this._store.state.game?.deck?.topDiscard) return;
    this._source = { kind: "discard" };
    this._show();
  }

  close() {
    if (!this._open) return;
    this._open = false;
    this._root.hidden = true;
    this._root.innerHTML = "";
    document.removeEventListener("keydown", this._onKey, true);
    this._returnFocus?.focus?.({ preventScroll: true });
    this._returnFocus = null;
  }

  _show() {
    if (!this._open) {
      this._open = true;
      this._returnFocus = document.activeElement;
      document.addEventListener("keydown", this._onKey, true);
    }
    this._render();
    this._root.hidden = false;
    this._root.querySelector(".inspector__close")?.focus({ preventScroll: true });
  }

  _refresh() {
    if (this._open) this._render();
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------

  _currentCard() {
    const src = this._source;
    if (!src) return null;
    if (src.kind === "discard") return this._store.state.game?.deck?.topDiscard ?? null;
    return this._hand().find(c => c.uid === src.uid) ?? null;
  }

  _hand() {
    return this._store.myPlayer?.hand ?? [];
  }

  _render() {
    const card = this._currentCard();
    if (!card) { this.close(); return; }

    const hand  = this._source.kind === "hand" ? this._hand() : [];
    const index = hand.findIndex(c => c.uid === card.uid);
    const suit  = SUIT_LABELS[card.suit] ?? card.suit;
    const where = this._source.kind === "discard" ? "Top of the discard pile" : "";

    this._root.innerHTML = `
      <div class="inspector__backdrop" data-close></div>
      <div class="inspector__sheet" role="document">
        <button class="inspector__close" type="button" data-close aria-label="Close card view">✕</button>
        <div class="inspector__card">${CardView.buildInspectCardHTML(card)}</div>
        <div class="inspector__text">
          ${where ? `<p class="inspector__where">${_esc(where)}</p>` : ""}
          <h2 class="inspector__name" id="inspector-title">${_esc(card.name)}</h2>
          <p class="inspector__meta">${_esc(card.rank)} of ${_esc(suit)}</p>
          ${card.description
            ? `<p class="inspector__desc">${_esc(card.description)}</p>`
            : `<p class="inspector__desc inspector__desc--none">No card text available.</p>`}
        </div>
        ${hand.length > 1 ? `
          <div class="inspector__nav">
            <button class="btn btn--ghost btn--small" type="button" data-nav="-1" aria-label="Previous card">‹ Prev</button>
            <span class="inspector__count">${index + 1} / ${hand.length}</span>
            <button class="btn btn--ghost btn--small" type="button" data-nav="1" aria-label="Next card">Next ›</button>
          </div>` : ""}
      </div>
    `;
    this._root.setAttribute("aria-labelledby", "inspector-title");
  }

  _step(delta) {
    if (this._source?.kind !== "hand") return;
    const hand = this._hand();
    if (hand.length < 2) return;
    const i = hand.findIndex(c => c.uid === this._source.uid);
    const next = hand[(i + delta + hand.length) % hand.length];
    this._source = { kind: "hand", uid: next.uid };
    this._render();
    this._root.querySelector(`[data-nav="${delta}"]`)?.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------
  // Input wiring
  // ------------------------------------------------------------------

  _wireRoot() {
    this._root.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) { this.close(); return; }
      const nav = e.target.closest("[data-nav]");
      if (nav) this._step(parseInt(nav.dataset.nav, 10));
    });

    this._onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); this.close(); return; }
      if (e.key === "ArrowLeft")  { this._step(-1); return; }
      if (e.key === "ArrowRight") { this._step(1);  return; }
      if (e.key === "Tab") this._trapFocus(e);
    };
  }

  _trapFocus(e) {
    const items = [...this._root.querySelectorAll("button")];
    if (items.length === 0) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  _wireHand() {
    const hand = this._els.handArea;
    if (!hand) return;

    let timer = null, startX = 0, startY = 0, pressedUid = null;
    const cancel = () => { clearTimeout(timer); timer = null; pressedUid = null; };
    const cardUid = (e) => e.target.closest?.(".hand-card[data-uid]")?.dataset.uid ?? null;

    hand.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      pressedUid = cardUid(e);
      if (!pressedUid) return;
      startX = e.clientX; startY = e.clientY;
      timer = setTimeout(() => {
        const uid = pressedUid;
        cancel();
        this._suppressClick = true;          // the lift-off must not also select the card
        this.openHandCard(uid);
      }, LONG_PRESS_MS);
    });
    hand.addEventListener("pointermove", (e) => {
      if (timer && Math.hypot(e.clientX - startX, e.clientY - startY) > MOVE_TOLERANCE) cancel();
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(t => hand.addEventListener(t, cancel));
    hand.addEventListener("scroll", cancel, { passive: true });

    // Right-click / long-press context menu → inspect instead of the browser menu.
    hand.addEventListener("contextmenu", (e) => {
      const uid = cardUid(e);
      if (!uid) return;
      e.preventDefault();
      cancel();
      this.openHandCard(uid);
    });

    // Capture phase: runs before the renderer's per-card click handlers.
    hand.addEventListener("click", (e) => {
      const uid = cardUid(e);
      if (!uid) return;
      if (this._suppressClick) {
        this._suppressClick = false;
        e.stopPropagation(); e.preventDefault();
        return;
      }
      if (!this._tapPlaysCard()) {
        e.stopPropagation(); e.preventDefault();
        this.openHandCard(uid);
      }
    }, true);
  }

  /** True when a tap on a hand card has a game meaning (select / discard). */
  _tapPlaysCard() {
    const phase = this._store.currentTurnPhase;
    return this._store.isMyTurn && (phase === "play" || phase === "discard");
  }

  _wireDiscard() {
    const el = this._els.discardTop;
    if (!el) return;
    el.addEventListener("click", () => this.openDiscardTop());
  }

  _wireActionButton() {
    const bar = this._els.actionBar;
    this._inspectBtn = bar?.querySelector("[data-action='inspect']") ?? null;
    this._inspectBtn?.addEventListener("click", () => {
      const uid = this._store.state.ui.selectedCardUid;
      if (uid) this.openHandCard(uid);
    });
  }

  _syncInspectButton(selectedUid) {
    if (this._inspectBtn) this._inspectBtn.hidden = !selectedUid;
  }
}

function _esc(str) {
  if (str == null) return "";
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
