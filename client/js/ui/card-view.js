/**
 * client/js/ui/card-view.js
 *
 * CardView — pure functions that build card HTML strings.
 *
 * WHY PURE FUNCTIONS NOT A CLASS:
 *   Cards are rendered in lists (hand, equipment, discard pile).
 *   Pure functions that return HTML strings are the fastest approach:
 *   - No DOM allocation until innerHTML is set.
 *   - Easy to test in isolation.
 *   - No "this" binding surprises.
 *
 * CARD DATA SHAPES (from server):
 *   Hand card (private):
 *     { uid, id, name, suit, rank, description }
 *
 *   Equipment / discard card (public):
 *     { uid, id, name, suit, rank }
 *
 *   Opponent hand:
 *     shown only as a count — no card identity exposed.
 *
 * RENDERING EFFICIENCY:
 *   The hand is re-rendered on every "store:hand_updated" event.
 *   With up to 12 cards this is trivial.  If hands ever get larger,
 *   switch to a keyed diffing approach (similar to lit-html).
 *
 *   Each card renders its suit symbol using Unicode so there are zero
 *   image requests for card faces.  Card art images are layered on top
 *   via CSS background-image using the card's `id` as the filename stem.
 *   If the image is missing, the text-only fallback is still readable.
 */

const SUIT_SYMBOLS = {
  spades:   "♠",
  hearts:   "♥",
  diamonds: "♦",
  clubs:    "♣",
};

const SUIT_COLORS = {
  spades:   "black",
  hearts:   "red",
  diamonds: "red",
  clubs:    "black",
};

export const SUIT_LABELS = { spades: "piky", hearts: "srdce", diamonds: "káry", clubs: "kříže" };

export const CardView = {

  /**
   * Build an HTML string for a card in the local player's hand.
   *
   * @param {{ uid, id, name, suit, rank, description }} card
   * @param {{ isSelected: boolean, isPlayable: boolean }}  opts
   * @returns {string} HTML
   */
  buildHandCardHTML(card, { isSelected = false, isPlayable = false } = {}) {
    const symbol = SUIT_SYMBOLS[card.suit] ?? "?";
    const color  = SUIT_COLORS[card.suit]  ?? "black";
    const classes = [
      "hand-card",
      `hand-card--suit-${card.suit}`,
      `hand-card--color-${color}`,
      isSelected  ? "hand-card--selected"  : "",
      isPlayable  ? "hand-card--playable"  : "",
      !isPlayable ? "hand-card--inactive"  : "",
    ].filter(Boolean).join(" ");

    return `
      <div class="${classes}"
           data-uid="${_attr(card.uid)}"
           data-id="${_attr(card.id)}"
           role="button"
           tabindex="0"
           aria-label="${_attr(card.name)} — ${_attr(card.suit)} ${_attr(card.rank)}"
           aria-pressed="${isSelected}"
           ${_artStyle(card)}>
        <div class="card__corner card__corner--top">
          <span class="card__rank">${_esc(card.rank)}</span>
          <span class="card__suit">${symbol}</span>
        </div>
        <div class="card__face">
          <div class="card__art" aria-hidden="true"></div>
          <div class="card__name">${_esc(card.name)}</div>
        </div>
        <div class="card__corner card__corner--bottom" aria-hidden="true">
          <span class="card__rank">${_esc(card.rank)}</span>
          <span class="card__suit">${symbol}</span>
        </div>
        ${isSelected ? '<div class="card__selected-indicator" aria-hidden="true">▶</div>' : ""}
        ${card.description ? `<div class="card__tooltip">${_esc(card.description)}</div>` : ""}
      </div>
    `;
  },

  /**
   * Build a large, non-interactive card face for the card inspector.
   * Same markup and styling as a hand card, just scaled by CSS; the
   * description is shown by the inspector next to it, not on the card.
   *
   * @param {{ id, name, suit, rank, has_art? }} card
   * @returns {string} HTML
   */
  buildInspectCardHTML(card) {
    const symbol = SUIT_SYMBOLS[card.suit] ?? "?";
    const color  = SUIT_COLORS[card.suit]  ?? "black";
    return `
      <div class="inspect-card inspect-card--suit-${_attr(card.suit)} inspect-card--color-${color}"
           ${_artStyle(card)}>
        <div class="card__corner card__corner--top">
          <span class="card__rank">${_esc(card.rank)}</span>
          <span class="card__suit">${symbol}</span>
        </div>
        <div class="card__face">
          <div class="card__art" aria-hidden="true"></div>
          <div class="card__name">${_esc(card.name)}</div>
        </div>
        <div class="card__corner card__corner--bottom" aria-hidden="true">
          <span class="card__rank">${_esc(card.rank)}</span>
          <span class="card__suit">${symbol}</span>
        </div>
      </div>
    `;
  },

  /**
   * Build HTML for the top card of the discard pile.
   * Uses public card shape — no description available.
   *
   * @param {{ id, name, suit, rank }} card
   * @returns {string} HTML
   */
  buildDiscardHTML(card) {
    const symbol = SUIT_SYMBOLS[card.suit] ?? "?";
    const color  = SUIT_COLORS[card.suit]  ?? "black";
    return `
      <div class="discard-card discard-card--suit-${card.suit} discard-card--color-${color}"
           ${_artStyle(card)}
           aria-label="Discard: ${_attr(card.name)}">
        <div class="card__corner card__corner--top">
          <span class="card__rank">${_esc(card.rank)}</span>
          <span class="card__suit">${symbol}</span>
        </div>
        <div class="card__face">
          <div class="card__art" aria-hidden="true"></div>
          <div class="card__name">${_esc(card.name)}</div>
        </div>
      </div>
    `;
  },

  /**
   * Build HTML for a face-down card (opponent hand or draw pile).
   * No card identity is revealed.
   *
   * @returns {string} HTML
   */
  buildFaceDownHTML() {
    return `
      <div class="card card--face-down" aria-hidden="true">
        <div class="card__back"></div>
      </div>
    `;
  },

  /**
   * Build a compact badge for an equipped blue card.
   *
   * @param {{ uid, id, name, suit, rank }} card
   * @returns {string} HTML
   */
  buildEquipmentBadgeHTML(card) {
    const symbol = SUIT_SYMBOLS[card.suit] ?? "?";
    return `
      <span class="equipment-badge equipment-badge--${_attr(card.id)}"
            title="${_attr(card.name)} (${_attr(card.suit)} ${_attr(card.rank)})">
        ${_esc(card.name)} ${symbol}
      </span>
    `;
  },
};

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

// Cards may opt out of art (placeholders, art not drawn yet) with has_art: false,
// which avoids a 404 per card and lets the CSS suit-pip fallback show.
function _artStyle(card) {
  if (card.has_art === false) return "";
  return `style="--card-art: url('assets/cards/${_attr(card.id)}.webp')"`;
}

function _esc(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function _attr(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
