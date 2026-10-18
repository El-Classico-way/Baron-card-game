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

  _onClose(ev) {
    this._bus.emit("socket:disconnected", { code: ev.code, reason: ev.reason });

    if (this._intentionalClose) return;

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
