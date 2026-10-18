/**
 * client/js/utils/dom.js
 *
 * DOM utilities and element registry.
 *
 * WHY QUERY ONCE AT STARTUP:
 *   Renderer calls element references hundreds of times per session.
 *   Querying the DOM by id/selector on every render is wasteful and error-
 *   prone (typos fail silently at query time, not at use time).
 *   Querying once at startup means:
 *     1. Any missing element is immediately visible in the console.
 *     2. All subsequent access is O(1) property lookup.
 *     3. Renderer constructor receives a plain object — easy to mock in tests.
 *
 * ELEMENT NAMING CONVENTION:
 *   camelCase property name  →  kebab-case HTML id
 *   e.g.  elements.handArea  →  id="hand-area"
 *        elements.myHpBar    →  id="my-hp-bar"
 *
 * OPTIONAL ELEMENTS:
 *   Elements marked optional are panels or overlays that may not exist in
 *   all game phases.  Renderer already null-guards before using them.
 */

/**
 * Query all required and optional DOM elements once.
 * Logs warnings for any missing required elements.
 *
 * @returns {object} elements
 */
export function queryElements() {
  const required = {
    // Panels
    joinPanel:          "join-panel",
    lobbyPanel:         "lobby-panel",
    gamePanel:          "game-panel",

    // Join form
    joinForm:           "join-form",
    roomIdInput:        "room-id-input",
    displayNameInput:   "display-name-input",
    joinBtn:            "join-btn",

    // Lobby
    roomIdDisplay:      "room-id-display",
    lobbyPlayerList:    "lobby-player-list",
    startGameBtn:       "start-game-btn",

    // Game — table areas
    opponentsArea:      "opponents-area",
    handArea:           "hand-area",
    deckCount:          "deck-count",
    discardCount:       "discard-count",
    discardTop:         "discard-top",

    // Game — local player panel
    myPlayerArea:       "my-player-area",
    myHpDisplay:        "my-hp-display",
    myHpBar:            "my-hp-bar",

    // Game — HUD
    turnIndicator:      "turn-indicator",
    actionBar:          "action-bar",
    rolePanel:          "role-panel",

    // Overlays and feedback
    notificationArea:   "notification-area",
    connectionBanner:   "connection-banner",
    gameOverlay:        "game-overlay",
    reactionOverlay:    "reaction-overlay",

    // Chat
    chatMessages:       "chat-messages",
    chatInput:          "chat-input",
    chatForm:           "chat-form",
  };

  const elements = {};
  const missing  = [];

  for (const [key, id] of Object.entries(required)) {
    const el = document.getElementById(id);
    if (!el) {
      missing.push(`#${id} (elements.${key})`);
    }
    elements[key] = el;   // null if missing — renderer null-guards
  }

  if (missing.length > 0) {
    console.warn(
      "[DOM] Missing elements — some UI features may not render:\n  " +
      missing.join("\n  ")
    );
  }

  return elements;
}

// ---------------------------------------------------------------------------
// Small DOM helpers used across modules
// ---------------------------------------------------------------------------

/**
 * Add an event listener and return a cleanup function.
 *
 * @param {Element}  el
 * @param {string}   event
 * @param {Function} handler
 * @returns {Function} removeListener
 */
export function on(el, event, handler) {
  if (!el) return () => {};
  el.addEventListener(event, handler);
  return () => el.removeEventListener(event, handler);
}

/**
 * Set element visibility.  Uses hidden attribute (CSS: [hidden] { display:none }).
 *
 * @param {Element}  el
 * @param {boolean}  visible
 */
export function setVisible(el, visible) {
  if (!el) return;
  el.hidden = !visible;
}

/**
 * Escape a string for safe insertion as text content.
 * For use when not going through innerHTML (direct textContent is always safe,
 * but this is useful when building template strings outside CardView).
 *
 * @param {string} str
 * @returns {string}
 */
export function esc(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Debounce a function.
 *
 * @param {Function} fn
 * @param {number}   ms
 * @returns {Function}
 */
export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
