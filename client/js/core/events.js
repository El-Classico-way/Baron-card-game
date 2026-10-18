/**
 * client/js/core/events.js
 *
 * Lightweight synchronous pub/sub event bus.
 *
 * WHY A DEDICATED BUS:
 *   Every module (socket, store, renderer, UI) emits and listens for events.
 *   Without a bus, modules import each other directly, creating a tangle of
 *   circular dependencies.  The bus inverts this — everyone knows the bus,
 *   nobody needs to know each other.
 *
 * EVENT NAMING CONVENTION:
 *   "server:<packet_type>"  — raw packets forwarded from the WebSocket
 *                              e.g. "server:game_event", "server:state_snapshot"
 *   "store:<topic>"         — state changes emitted by the store after
 *                              it processes a server event
 *                              e.g. "store:hand_updated", "store:turn_changed"
 *   "ui:<action>"           — user interactions emitted by the UI layer
 *                              e.g. "ui:card_clicked", "ui:end_turn_clicked"
 *   "socket:<lifecycle>"    — connection state changes
 *                              e.g. "socket:connected", "socket:disconnected"
 *
 * USAGE:
 *   const bus = new EventBus();
 *
 *   const off = bus.on("store:turn_changed", ({ playerId }) => {
 *     console.log("Now it's", playerId, "turn");
 *   });
 *   off(); // unsubscribe
 *
 *   bus.once("server:joined", (payload) => { … });
 *
 *   bus.emit("ui:card_clicked", { cardUid: "bang-abc123" });
 */

export class EventBus {
  constructor() {
    /** @type {Map<string, Set<Function>>} */
    this._listeners = new Map();
  }

  /**
   * Subscribe to an event.  Returns an unsubscribe function.
   *
   * @param {string}   event
   * @param {Function} handler
   * @returns {Function} unsubscribe
   */
  on(event, handler) {
    if (!this._listeners.has(event)) {
      this._listeners.set(event, new Set());
    }
    this._listeners.get(event).add(handler);

    return () => this.off(event, handler);
  }

  /**
   * Subscribe for a single emission, then auto-unsubscribe.
   *
   * @param {string}   event
   * @param {Function} handler
   */
  once(event, handler) {
    const off = this.on(event, (data) => {
      off();
      handler(data);
    });
    return off;
  }

  /**
   * Unsubscribe a specific handler.
   *
   * @param {string}   event
   * @param {Function} handler
   */
  off(event, handler) {
    this._listeners.get(event)?.delete(handler);
  }

  /**
   * Emit an event synchronously to all registered handlers.
   * Errors in handlers are caught and logged so one bad handler
   * never breaks the others.
   *
   * @param {string} event
   * @param {*}      data
   */
  emit(event, data) {
    const handlers = this._listeners.get(event);
    if (!handlers) return;

    for (const handler of handlers) {
      try {
        handler(data);
      } catch (err) {
        console.error(`[EventBus] Error in handler for "${event}":`, err);
      }
    }
  }

  /**
   * Remove all listeners for an event (or all events if no argument).
   *
   * @param {string} [event]
   */
  clear(event) {
    if (event) {
      this._listeners.delete(event);
    } else {
      this._listeners.clear();
    }
  }
}

// Singleton — shared across the whole application.
export const bus = new EventBus();
