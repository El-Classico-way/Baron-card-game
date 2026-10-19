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
import { CardInspector } from "./ui/card-inspector.js";
import { VydrzView }     from "./vydrz/view.js";
import { ManualView }    from "./vydrz/manual-view.js";
import { Numpad }        from "./vydrz/numpad.js";
import { DealIntro }     from "./vydrz/deal-intro.js";
import { confirmDialog } from "./ui/confirm.js";
import { MockSocket }   from "./mock/mock-socket.js";
import { Store }        from "./core/store.js";
import { Renderer }     from "./game/renderer.js";
import { Controller }   from "./game/controller.js";
import { Animator }     from "./game/animator.js";
import { queryElements, on, debounce } from "./utils/dom.js";

// Declared before boot() runs: boot() executes at import time and reads it.
const DEMO_KEY = "baron.demo";

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

  // 4. Socket — real server, or the in-browser mock when opened with ?mock
  //    (?mock=1 → demo lobby, ?mock=table → straight to the table, &bots=N).
  const mock = _mockOptions();
  const socket = mock ? new MockSocket(mock) : new SocketClient(SERVER_URL, bus);

  // 5. Renderer
  const renderer = new Renderer(store, elements);

  // 6. Controller
  const controller = new Controller(store, socket);

  // 7. Animator
  const animator = new Animator(store);

  // Card inspector (enlarged card view); see js/ui/card-inspector.js.
  const inspector = new CardInspector(store, elements);

  // Výdrž table (rendered only when the server runs that mode).
  const dealIntro = new DealIntro(document.getElementById("deal-intro"));
  const vydrz = new VydrzView({
    socket, inspector, dealIntro,
    getHostId: () => store.state.lobby.hostPlayerId,
    onLeave: () => _leave(socket),
    onConfirmLeave: () => leaveGame(socket),
  });
  const openSettings = () => vydrz._openSettings();
  const manual = new ManualView({
    socket, inspector, openSettings, dealIntro,
    numpad: new Numpad(document.getElementById("vm-keypad")),
    getHostId: () => store.state.lobby.hostPlayerId,
    onLeave: () => _leave(socket),
    onConfirmLeave: () => leaveGame(socket),
  });
  on(document.getElementById("lobby-settings-btn"), "click", openSettings);
  for (const id of ["mode-switch", "variant-switch"]) {
    on(document.getElementById(id), "click", (e) => {
      const btn = e.target.closest("[data-mode]");
      if (btn && !btn.disabled) socket.send("set_mode", { mode: btn.dataset.mode });
    });
  }

  _wireSessionEvents(socket, store, elements, [vydrz, manual]);
  _wireOverlayEdges();

  // 8. Wire join / lobby forms
  _wireJoinForm(elements, socket);
  _wireLobbyControls(elements, socket, store);
  _wireChatForm(elements);
  _wireChatSheet();
  _wireActionBar(elements);
  _wireLeaveButtons(socket);
  on(document.getElementById("demo-btn"), "click", () => {
    try { sessionStorage.setItem(DEMO_KEY, "1"); } catch { /* demo via ?mock=1 still works */ }
    location.reload();
  });
  // A build without a server (tools/build_preview.py) can only show the demo.
  if (window.BARON_MOCK) document.body.classList.add("static-preview");

  // Expose to console for debugging in development.
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    window.__app = { store, socket, renderer, bus };
  }

  // 9. Mock mode skips the landing page; otherwise try to resume a session.
  if (mock) {
    const ribbon = document.getElementById("mock-ribbon");
    if (ribbon) ribbon.hidden = false;
    document.body.classList.add("has-ribbon");
    _showPanel(elements, "lobby");
    socket.connect("DEMO", "Ty");
    return;
  }
  const resumed = socket.tryResume();

  // 10. If no session, show join panel
  if (!resumed) {
    _showPanel(elements, "join");
    _prefillRoomFromUrl(elements);
    _showFlash(elements);
  } else {
    // Show lobby/game panel depending on state — snapshot will arrive
    // shortly and renderer will switch panels automatically.
    _showPanel(elements, "lobby");
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

    const roomId      = roomIdInput?.value.trim();
    const displayName = displayNameInput?.value.trim();
    // Two submit buttons: "Create a room" and "Join". Enter in the code
    // field joins, Enter in the name field creates.
    const mode = e.submitter?.dataset.mode ?? (roomId ? "join" : "create");

    if (!displayName) {
      _showFormError(joinForm, "Nejdřív zadej jméno.");
      displayNameInput?.focus();
      return;
    }

    if (mode === "join") {
      if (!roomId) {
        _showFormError(joinForm, "Zadej kód místnosti, který ti kamarád poslal.");
        roomIdInput?.focus();
        return;
      }
      socket.connect(roomId, displayName);
    } else {
      _createRoomAndConnect(socket, displayName);
    }
  });
}

async function _createRoomAndConnect(socket, displayName) {
  try {
    const res  = await fetch("lobby/rooms", { method: "POST" });
    const data = await res.json();

    if (!data.room_id) throw new Error("No room_id in response");

    // Update URL so the user can share it.
    history.replaceState({}, "", `?room=${encodeURIComponent(data.room_id)}`);

    socket.connect(data.room_id, displayName);
  } catch (err) {
    console.error("[App] Failed to create room:", err);
    bus.emit("store:notification", {
      message: "Místnost se nepodařilo vytvořit. Běží server?",
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

function _showFormError(form, message, ttl = 4000) {
  let errEl = form.querySelector(".form-error");
  if (!errEl) {
    errEl = document.createElement("p");
    errEl.className = "form-error";
    form.appendChild(errEl);
  }
  errEl.textContent = message;
  setTimeout(() => errEl?.remove(), ttl);
}

// ---------------------------------------------------------------------------
// Lobby controls
// ---------------------------------------------------------------------------

function _wireLobbyControls(elements, socket, store) {
  const { startGameBtn } = elements;

  // Start game: host only; the server rejects anyone else.
  if (startGameBtn) {
    on(startGameBtn, "click", () => {
      socket.send("start_game");
      bus.emit("ui:start_game_clicked", {});
    });
  }

  on(document.getElementById("copy-link-btn"), "click", () => _copyCode(store));

  // When the server transitions us to playing, show the game panel.
  bus.on("store:game_started", () => _showPanel(elements, "game"));
  // (Re)connecting must not pull someone out of a game that is on screen.
  bus.on("store:joined", () => {
    const inGame = ["vydrz-panel", "vm-panel", "game-panel"].some(id => document.getElementById(id)?.hidden === false);
    if (!inGame) _showPanel(elements, "lobby");
  });
}

/**
 * Full-screen overlays scroll themselves (.vr, .vs). Their dimming backdrop is a fixed child that
 * ignores pointer events, otherwise the mouse wheel over the screen edges would not scroll
 * them. A click on the bare overlay area outside the panel therefore closes it like the
 * backdrop used to.
 */
function _wireOverlayEdges() {
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!t?.classList || !(t.classList.contains("vr") || t.classList.contains("vs"))) return;
    t.querySelector(".vr__backdrop, .vs__backdrop")?.click();
  });
}

const FLASH_KEY = "baron.flash";

function _showFlash(elements) {
  let msg = null;
  try { msg = sessionStorage.getItem(FLASH_KEY); sessionStorage.removeItem(FLASH_KEY); } catch { /* no storage */ }
  if (msg && elements.joinForm) _showFormError(elements.joinForm, msg, 12000);
}

/**
 * Session-level events:
 *  - the server refused the session for good (expired, name taken…) → back to the landing page with the reason;
 *  - the host sent everybody back to the lobby → drop the finished game and show the lobby.
 */
function _wireSessionEvents(socket, store, elements, views) {
  bus.on("socket:fatal", ({ message }) => {
    try { sessionStorage.clear(); sessionStorage.setItem(FLASH_KEY, message || "Spojení s hrou se nepodařilo obnovit."); } catch { /* no storage */ }
    try { history.replaceState({}, "", location.pathname); } catch { /* sandboxed */ }
    location.reload();
  });
  bus.on("store:lobby_updated", () => {
    if (store.state.lobby.gamePhase !== "lobby") return;
    const inGame = ["vydrz-panel", "vm-panel"].some(id => document.getElementById(id)?.hidden === false);
    if (!inGame) return;
    for (const v of views) v.reset();
    _showPanel(elements, "lobby");
  });
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


// ---------------------------------------------------------------------------
// Mock mode, invite link, chat sheet, leave
// ---------------------------------------------------------------------------

function _mockOptions() {
  const params = new URLSearchParams(location.search);
  if (params.has("mock")) {
    const bots = parseInt(params.get("bots") ?? "3", 10);
    // ?mode=rezba selects the older action-game prototype in the mock (dev/testing only).
    return { bots: Number.isFinite(bots) ? bots : 3, autostart: params.get("mock") === "table", mode: params.get("mode") };
  }
  // Demo started from the landing page ("Vyzkoušet ukázku"); cleared when leaving.
  try { if (sessionStorage.getItem(DEMO_KEY)) return { bots: 3, autostart: false, mode: null }; } catch { /* no storage */ }
  return null;
}

async function _copyCode(store) {
  const roomId = store.state.roomId;
  if (!roomId) return;
  try {
    await navigator.clipboard.writeText(roomId);
    bus.emit("store:notification", { message: "Kód zkopírován", type: "success", id: Date.now() });
  } catch {
    window.prompt("Zkopíruj kód místnosti:", roomId);      // clipboard unavailable (e.g. plain http)
  }
}

function _wireChatSheet() {
  const sheet  = document.getElementById("chat-sheet");
  const toggle = document.getElementById("chat-toggle");
  const close  = document.getElementById("chat-close");
  if (!sheet || !toggle) return;
  const set = (open) => {
    sheet.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", String(open));
  };
  on(toggle, "click", () => set(!sheet.classList.contains("is-open")));
  on(close,  "click", () => set(false));
}

/** Leave for good: tell the server, forget the session and go back to the landing page. */
function _leave(socket) {
  try { socket.sendLeaveRoom?.(); } catch { /* not connected */ }
  try { socket.disconnect(); } catch { /* already closed */ }       // also clears the stored session
  try { sessionStorage.removeItem(DEMO_KEY); } catch { /* no storage */ }
  try { history.replaceState({}, "", location.pathname); } catch { /* sandboxed */ }   // drop ?mock / ?room
  location.reload();
}

/** Ask first when a game is running. */
export async function leaveGame(socket) {
  const ok = await confirmDialog({
    title: "Opustit hru?", text: "Hra poběží dál bez tebe (za tebe se bude hrát automaticky). Připojit se zpět můžeš pod stejným jménem.", ok: "Opustit", cancel: "Zůstat", danger: true,
  });
  if (ok) _leave(socket);
}

function _wireLeaveButtons(socket) {
  on(document.getElementById("leave-btn"), "click", () => _leave(socket));
  on(document.getElementById("leave-game-btn"), "click", () => leaveGame(socket));
}
