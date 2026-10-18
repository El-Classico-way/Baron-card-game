/**
 * client/js/app.js
 *
 * Application entry point — wires all modules together.
 *
 * STARTUP SEQUENCE:
 *   1. Query DOM elements once (dom.js).
 *   2. Create EventBus singleton (already exported from events.js).
 *   3. Create Store (subscribes to server:* events internally).
 *   4. Create SocketClient (emits server:* events to bus).
 *   5. Create Renderer (subscribes to store:* events internally).
 *   6. Create Controller (subscribes to ui:* events internally).
 *   7. Create Animator (subscribes to render:* events internally).
 *   8. Wire join form → socket.connect().
 *   9. Try to resume an existing session (reconnect_token in sessionStorage).
 *  10. If no stored session, show the join panel.
 *
 * ROOM ID FROM URL:
 *   Navigating to /?room=abc123 pre-fills the room id input and lets
 *   players share a link.  The host creates a room via POST /lobby/rooms
 *   and shares the URL.
 *
 * MODULE GRAPH (no circular deps):
 *
 *   events.js  (singleton bus — imported by everyone)
 *       ↑
 *   socket.js  ──emit──▶  bus  ──"server:*"──▶  store.js
 *                                                   │
 *                         bus  ◀──"ui:*"──────  controller.js
 *                                                   │ reads
 *                         bus  ──"store:*"──▶  renderer.js
 *                                                   │ emits
 *                         bus  ──"render:*"──▶ animator.js
 */

import { bus }         from "./core/events.js";
import { SocketClient } from "./core/socket.js";
import { Store }        from "./core/store.js";
import { Renderer }     from "./game/renderer.js";
import { Controller }   from "./game/controller.js";
import { Animator }     from "./game/animator.js";
import { queryElements, on, debounce } from "./utils/dom.js";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const SERVER_URL = (() => {
  // In development: ws://localhost:8000
  // In production:  wss://your-domain.com
  // Auto-detect: use same host, swap http→ws
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}`;
})();

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

function boot() {
  // 1. Query elements
  const elements = queryElements();

  // 2. Bus is already the singleton from events.js

  // 3. Store
  const store = new Store();

  // 4. Socket
  const socket = new SocketClient(SERVER_URL, bus);

  // 5. Renderer
  const renderer = new Renderer(store, elements);

  // 6. Controller
  const controller = new Controller(store, socket);

  // 7. Animator
  const animator = new Animator(store);

  // 8. Wire join / lobby forms
  _wireJoinForm(elements, socket);
  _wireLobbyControls(elements, socket, store);
  _wireChatForm(elements);
  _wireActionBar(elements);

  // 9. Try to resume session from sessionStorage
  const resumed = socket.tryResume();

  // 10. If no session, show join panel
  if (!resumed) {
    _showPanel(elements, "join");
    _prefillRoomFromUrl(elements);
  } else {
    // Show lobby/game panel depending on state — snapshot will arrive
    // shortly and renderer will switch panels automatically.
    _showPanel(elements, "lobby");
  }

  // Expose to console for debugging in development.
  if (location.hostname === "localhost") {
    window.__app = { store, socket, renderer, bus };
  }
}

// ---------------------------------------------------------------------------
// Join form
// ---------------------------------------------------------------------------

function _wireJoinForm(elements, socket) {
  const { joinForm, roomIdInput, displayNameInput } = elements;
  if (!joinForm) return;

  on(joinForm, "submit", (e) => {
    e.preventDefault();

    const roomId     = roomIdInput?.value.trim();
    const displayName = displayNameInput?.value.trim();

    if (!displayName) {
      _showFormError(joinForm, "Please enter a display name.");
      return;
    }

    if (roomId) {
      // Join specific room
      socket.connect(roomId, displayName);
    } else {
      // Create a new room via HTTP, then connect.
      _createRoomAndConnect(socket, displayName);
    }
  });
}

async function _createRoomAndConnect(socket, displayName) {
  try {
    const res  = await fetch("/lobby/rooms", { method: "POST" });
    const data = await res.json();

    if (!data.room_id) throw new Error("No room_id in response");

    // Update URL so the user can share it.
    history.replaceState({}, "", `/?room=${encodeURIComponent(data.room_id)}`);

    socket.connect(data.room_id, displayName);
  } catch (err) {
    console.error("[App] Failed to create room:", err);
    bus.emit("store:notification", {
      message: "Could not create room. Is the server running?",
      type:    "error",
      id:      Date.now(),
    });
  }
}

function _prefillRoomFromUrl(elements) {
  const params = new URLSearchParams(location.search);
  const roomId = params.get("room");
  if (roomId && elements.roomIdInput) {
    elements.roomIdInput.value = roomId;
  }
}

function _showFormError(form, message) {
  let errEl = form.querySelector(".form-error");
  if (!errEl) {
    errEl = document.createElement("p");
    errEl.className = "form-error";
    form.appendChild(errEl);
  }
  errEl.textContent = message;
  setTimeout(() => errEl?.remove(), 4000);
}

// ---------------------------------------------------------------------------
// Lobby controls
// ---------------------------------------------------------------------------

function _wireLobbyControls(elements, socket, store) {
  const { startGameBtn } = elements;

  // Start game: host only, server will reject if not host.
  // We just send a signal via chat-style message for now; a dedicated
  // "start_game" packet type should be added to protocol.py.
  // TODO: add "start_game" message type to server protocol.
  // For now, the button text reminds the host to use the server console.
  if (startGameBtn) {
    on(startGameBtn, "click", () => {
      // Placeholder — actual start_game message needs server support.
      bus.emit("ui:start_game_clicked", {});
      console.info("[App] start_game_clicked — wire to server when ready.");
    });
  }

  // When the server transitions us to playing, show the game panel.
  bus.on("store:game_started", () => _showPanel(elements, "game"));
  bus.on("store:joined",       () => _showPanel(elements, "lobby"));
}

// ---------------------------------------------------------------------------
// Chat form
// ---------------------------------------------------------------------------

function _wireChatForm(elements) {
  const { chatForm, chatInput } = elements;
  if (!chatForm) return;

  on(chatForm, "submit", (e) => {
    e.preventDefault();
    const message = chatInput?.value.trim();
    if (!message) return;

    bus.emit("ui:chat_submitted", { message });
    if (chatInput) chatInput.value = "";
  });
}

// ---------------------------------------------------------------------------
// Action bar buttons
// ---------------------------------------------------------------------------

function _wireActionBar(elements) {
  const { actionBar } = elements;
  if (!actionBar) return;

  // Draw button
  const drawBtn = actionBar.querySelector("[data-action='draw']");
  on(drawBtn, "click", () => bus.emit("ui:draw_clicked", {}));

  // End turn button
  const endBtn = actionBar.querySelector("[data-action='end-turn']");
  on(endBtn, "click", () => bus.emit("ui:end_turn_clicked", {}));

  // Request sync (debug / reconnect helper)
  const syncBtn = actionBar.querySelector("[data-action='request-sync']");
  on(syncBtn, "click", () => bus.emit("ui:request_sync_clicked", {}));
}

// ---------------------------------------------------------------------------
// Panel management
// ---------------------------------------------------------------------------

function _showPanel(elements, name) {
  ["join", "lobby", "game"].forEach(n => {
    const el = elements[`${n}Panel`];
    if (el) el.hidden = n !== name;
  });
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------

// Wait for DOM to be ready.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
