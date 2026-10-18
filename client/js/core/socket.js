/**
 * client/js/core/socket.js
 *
 * WebSocket transport layer.
 *
 * RESPONSIBILITIES:
 *   - Open, maintain, and reconnect the WebSocket connection.
 *   - Serialise outbound messages to JSON.
 *   - Deserialise inbound frames and emit them to the EventBus.
 *   - Persist and reuse the reconnect_token across sessions.
 *
 * WHAT THIS MODULE IS NOT:
 *   - Not responsible for game logic.
 *   - Not responsible for rendering anything.
 *   - Not responsible for interpreting packet content — it just routes.
 *
 * RECONNECT STRATEGY:
 *   Exponential backoff starting at 1s, capped at 30s.  On each reconnect
 *   attempt the stored reconnect_token is sent so the server resumes the
 *   same player slot without creating a new player_id.
 *
 *   Token storage: sessionStorage (tab-scoped).  If the tab is closed and
 *   reopened before the server's 30s grace period expires, the token is
 *   still available.  localStorage would be cross-tab which could cause
 *   two tabs to fight over the same session — sessionStorage avoids that.
 *
 * PACKET CONTRACT (mirrors server/networking/protocol.py):
 *   All frames in both directions:  { "type": string, "payload": object }
 */

import { EventBus } from "./events.js";

const STORAGE_KEY_TOKEN   = "reconnect_token";
const STORAGE_KEY_ROOM    = "room_id";
const STORAGE_KEY_NAME    = "display_name";

const BACKOFF_BASE_MS     = 1_000;
const BACKOFF_MAX_MS      = 30_000;
const BACKOFF_FACTOR      = 2;

const PING_INTERVAL_MS    = 10_000;   // heartbeat: detects connections the OS silently killed
const PONG_TIMEOUT_MS     = 6_000;

// Server error codes after which retrying cannot help.
const FATAL_CODES = new Set(["invalid_token", "room_not_joinable", "name_taken", "missing_display_name"]);

export class SocketClient {
  /**
   * @param {string} serverUrl  Base WS URL, e.g. "ws://localhost:8000"
   * @param {EventBus} bus      Shared event bus
   */
  constructor(serverUrl, bus) {
    this._serverUrl  = serverUrl.replace(/\/$/, "");
    this._bus        = bus;
    this._ws         = null;
    this._roomId     = null;
    this._displayName = null;

    this._intentionalClose = false;
    this._reconnectAttempt = 0;
    this._reconnectTimer   = null;

    // Outbound queue: messages buffered while disconnected.
    // Flushed on reconnect so nothing is silently dropped.
    this._sendQueue = [];

    this._pingTimer = null;
    this._pongTimer = null;
    this._wireWakeEvents();
  }

  /**
   * Phones freeze background tabs and drop their sockets; when the page comes
   * back (or the network returns) reconnect immediately instead of waiting out
   * the backoff timer.
   */
  _wireWakeEvents() {
    const wake = () => this.reconnectNow();
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") wake(); });
    window.addEventListener("online", wake);
    window.addEventListener("pageshow", wake);
    window.addEventListener("focus", wake);
  }

  /** Reconnect right away if the connection is gone or looks dead. */
  reconnectNow() {
    if (this._intentionalClose || !this._roomId) return;
    const ws = this._ws;
    if (ws && ws.readyState === WebSocket.OPEN) {
      this._ping();                     // verify it is really alive; a dead one is replaced on timeout
      return;
    }
    if (ws && ws.readyState === WebSocket.CONNECTING) return;
    clearTimeout(this._reconnectTimer);
    this._reconnectAttempt = 0;
    this._open(sessionStorage.getItem(STORAGE_KEY_TOKEN) || null);
  }

  _ping() {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return;
    try { this._ws.send(JSON.stringify({ type: "ping", payload: {} })); } catch { /* closing */ }
    clearTimeout(this._pongTimer);
    this._pongTimer = setTimeout(() => this._dropDead(), PONG_TIMEOUT_MS);
  }

  _dropDead() {
    const ws = this._ws;
    if (!ws) return;
    ws.onclose = null;
    try { ws.close(); } catch { /* already gone */ }
    this._stopHeartbeat();
    this._bus.emit("socket:disconnected", { code: 0, reason: "timeout" });
    this._scheduleReconnect(true);
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this._pingTimer = setInterval(() => this._ping(), PING_INTERVAL_MS);
  }

  _stopHeartbeat() {
    clearInterval(this._pingTimer);
    clearTimeout(this._pongTimer);
  }

  // ------------------------------------------------------------------
  // Connection management
  // ------------------------------------------------------------------

  /**
   * Connect to a room for the first time.
   * Clears any stale session token so we get a fresh player slot.
   *
   * @param {string} roomId
   * @param {string} displayName
   */
  connect(roomId, displayName) {
    this._roomId      = roomId;
    this._displayName = displayName;

    // Clear stale token — new join, fresh slot.
    sessionStorage.removeItem(STORAGE_KEY_TOKEN);
    sessionStorage.setItem(STORAGE_KEY_ROOM, roomId);
    sessionStorage.setItem(STORAGE_KEY_NAME, displayName);

    this._intentionalClose = false;
    this._reconnectAttempt = 0;
    this._open();
  }

  /**
   * Attempt to resume a previous session from sessionStorage.
   * Returns true if a stored session was found and a reconnect was initiated.
   *
   * Call this on page load before showing the join form.
   */
  tryResume() {
    const token = sessionStorage.getItem(STORAGE_KEY_TOKEN);
    const roomId = sessionStorage.getItem(STORAGE_KEY_ROOM);
    const name   = sessionStorage.getItem(STORAGE_KEY_NAME);

    if (!token || !roomId) return false;

    this._roomId       = roomId;
    this._displayName  = name;
    this._intentionalClose = false;
    this._reconnectAttempt = 0;
    this._open(token);
    return true;
  }

  /**
   * Gracefully close — does not attempt reconnect.
   */
  disconnect() {
    this._intentionalClose = true;
    this._stopHeartbeat();
    clearTimeout(this._reconnectTimer);
    if (this._ws) {
      this._ws.close(1000, "user_disconnect");
    }
    sessionStorage.clear();
  }

  // ------------------------------------------------------------------
  // Sending
  // ------------------------------------------------------------------

  /**
   * Send a typed packet to the server.
   * If the socket is not open, the packet is queued and flushed on reconnect.
   *
   * @param {string} type     Message type key (e.g. "play_card")
   * @param {object} payload  Payload object
   */
  send(type, payload = {}) {
    const frame = JSON.stringify({ type, payload });

    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(frame);
    } else {
      // Buffer while reconnecting — server will handle idempotency.
      this._sendQueue.push(frame);
    }
  }

  // ------------------------------------------------------------------
  // Convenience senders (mirrors server inbound payload models)
  // ------------------------------------------------------------------

  sendDrawCards(count = 2)               { this.send("draw_cards",  { count }); }
  sendPlayCard(cardUid, targetId = null) { this.send("play_card",   { card_uid: cardUid, target_id: targetId }); }
  sendDiscardCard(cardUid)               { this.send("discard_card",{ card_uid: cardUid }); }
  sendEndTurn()                          { this.send("end_turn",    {}); }
  sendRequestSnapshot()                  { this.send("request_snapshot", {}); }
  sendChat(message)                      { this.send("chat",        { message }); }
  sendLeaveRoom()                        { this.send("leave_room",  {}); }

  // ------------------------------------------------------------------
  // Internal WebSocket lifecycle
  // ------------------------------------------------------------------

  _open(reconnectToken = null) {
    if (this._ws) {
      this._ws.onclose = null;   // suppress the auto-reconnect from the old socket
      this._ws.close();
    }

    const url = this._buildUrl(reconnectToken);
    this._ws  = new WebSocket(url);

    this._ws.onopen    = () => this._onOpen();
    this._ws.onmessage = (ev) => this._onMessage(ev);
    this._ws.onerror   = (ev) => this._onError(ev);
    this._ws.onclose   = (ev) => this._onClose(ev);
  }

  _buildUrl(reconnectToken) {
    const base = `${this._serverUrl}/ws/${encodeURIComponent(this._roomId)}`;

    if (reconnectToken) {
      return `${base}?token=${encodeURIComponent(reconnectToken)}`;
    }
    return `${base}?display_name=${encodeURIComponent(this._displayName)}`;
  }

  _onOpen() {
    this._reconnectAttempt = 0;
    this._startHeartbeat();
    this._bus.emit("socket:connected", {});

    // Flush queued outbound messages.
    while (this._sendQueue.length > 0) {
      this._ws.send(this._sendQueue.shift());
    }
  }

  _onMessage(ev) {
    let packet;
    try {
      packet = JSON.parse(ev.data);
    } catch {
      console.warn("[Socket] Non-JSON frame received:", ev.data);
      return;
    }

    const { type, payload } = packet;
    if (!type) {
      console.warn("[Socket] Packet missing 'type':", packet);
      return;
    }

    clearTimeout(this._pongTimer);     // any traffic proves the connection is alive
    if (type === "pong") return;

    if (type === "error" && FATAL_CODES.has(payload?.code)) {
      this._fatal(payload);
      return;
    }

    // Store reconnect token the moment we receive it.
    if (type === "joined" && payload?.reconnect_token) {
      sessionStorage.setItem(STORAGE_KEY_TOKEN, payload.reconnect_token);
      sessionStorage.setItem(STORAGE_KEY_ROOM, payload.room_id);
    }

    // Route every packet to the event bus using "server:<type>" convention.
    // Subscribers listen on e.g. "server:game_event", "server:state_snapshot".
    this._bus.emit(`server:${type}`, payload);
  }

  _onError(ev) {
    console.error("[Socket] Error:", ev);
    this._bus.emit("socket:error", { event: ev });
  }

  /** The server will never accept this session again: stop retrying, forget it, tell the UI. */
  _fatal(payload) {
    this._intentionalClose = true;
    this._stopHeartbeat();
    clearTimeout(this._reconnectTimer);
    const ws = this._ws;
    if (ws) { ws.onclose = null; try { ws.close(); } catch { /* gone */ } }
    sessionStorage.removeItem(STORAGE_KEY_TOKEN);
    this._bus.emit("socket:disconnected", { code: 0, reason: "fatal" });
    this._bus.emit("socket:fatal", { code: payload.code, message: payload.message });
  }

  _onClose(ev) {
    this._stopHeartbeat();
    this._bus.emit("socket:disconnected", { code: ev.code, reason: ev.reason });
    if (this._intentionalClose) return;
    this._scheduleReconnect();
  }

  _scheduleReconnect() {
    clearTimeout(this._reconnectTimer);
    // Schedule reconnect with exponential backoff.
    const delay = Math.min(
      BACKOFF_BASE_MS * Math.pow(BACKOFF_FACTOR, this._reconnectAttempt),
      BACKOFF_MAX_MS
    );
    this._reconnectAttempt++;

    console.info(`[Socket] Reconnecting in ${delay}ms (attempt ${this._reconnectAttempt})…`);
    this._bus.emit("socket:reconnecting", { delay, attempt: this._reconnectAttempt });

    this._reconnectTimer = setTimeout(() => {
      const token = sessionStorage.getItem(STORAGE_KEY_TOKEN);
      this._open(token || null);
    }, delay);
  }
}
