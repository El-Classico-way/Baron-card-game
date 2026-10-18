/**
 * client/js/game/controller.js
 *
 * Controller — the only module that calls socket.send*().
 *
 * RESPONSIBILITIES:
 *   - Listen to "ui:*" events emitted by the renderer and card-view.
 *   - Validate that an action is currently legal (phase, turn, selection).
 *   - Call the appropriate socket.send*() method.
 *   - Set ui.pendingAction so the renderer can disable buttons.
 *
 * WHAT THE CONTROLLER IS NOT:
 *   - Not responsible for rendering anything.
 *   - Not responsible for game rules (server enforces those).
 *   - Not responsible for state management.
 *
 * ACTION FLOW:
 *   User taps "Draw" button
 *     → ui:draw_clicked emitted by ActionBar button listener
 *     → Controller._onDrawClicked()
 *     → store.setPendingAction("draw")   [disables UI]
 *     → socket.sendDrawCards()           [sends to server]
 *     → server sends action_ack          [store clears pending]
 *     → renderer re-enables UI
 *
 * TWO-STEP PLAY (select then target):
 *   1. User clicks a card → ui:card_clicked
 *      → if phase is "play": store.selectCard(uid)  (no send yet)
 *      → renderer highlights card + shows "pick a target" message
 *   2a. Card needs no target (Beer, Stagecoach):
 *      → controller checks card needs_target=false
 *      → immediately sends play_card with no target_id
 *   2b. Card needs a target (Bang!, Panic!):
 *      → UI shows "select a target" prompt
 *      → user clicks opponent → ui:target_clicked
 *      → controller sends play_card(selectedCardUid, targetPlayerId)
 *   Escape: clicking same card again deselects.
 */

import { bus } from "../core/events.js";

// Cards that can be played without a target (expand as needed).
// Ultimately the server validates — this is just a UX hint.
const NO_TARGET_CARD_IDS = new Set([
  "beer", "stagecoach", "wells_fargo", "gatling", "indians",
]);

export class Controller {
  /**
   * @param {Store}        store
   * @param {SocketClient} socket
   */
  constructor(store, socket) {
    this._store  = store;
    this._socket = socket;
    this._subscribe();
  }

  // ------------------------------------------------------------------
  // Event subscriptions
  // ------------------------------------------------------------------

  _subscribe() {
    // User interaction events
    bus.on("ui:draw_clicked",        () => this._onDrawClicked());
    bus.on("ui:end_turn_clicked",    () => this._onEndTurnClicked());
    bus.on("ui:card_clicked",        (d) => this._onCardClicked(d));
    bus.on("ui:target_clicked",      (d) => this._onTargetClicked(d));
    bus.on("ui:discard_clicked",     (d) => this._onDiscardClicked(d));
    bus.on("ui:chat_submitted",      (d) => this._onChatSubmitted(d));
    bus.on("ui:skip_reaction_clicked",()  => this._onSkipReaction());
    bus.on("ui:play_again_clicked",  ()  => this._onPlayAgain());
    bus.on("ui:request_sync_clicked",()  => this._onRequestSync());

    // Reconnect → request fresh snapshot
    bus.on("socket:connected",       ()  => {
      if (this._store.state.game) {
        this._socket.sendRequestSnapshot();
      }
    });
  }

  // ------------------------------------------------------------------
  // Draw phase
  // ------------------------------------------------------------------

  _onDrawClicked() {
    if (!this._canAct("draw")) return;
    this._store.setPendingAction("draw");
    this._socket.sendDrawCards(2);
  }

  // ------------------------------------------------------------------
  // Play phase — two-step flow
  // ------------------------------------------------------------------

  _onCardClicked({ cardUid, cardId }) {
    const { ui } = this._store.state;
    const phase  = this._store.currentTurnPhase;

    if (!this._store.isMyTurn) return;

    // --- DISCARD PHASE: clicking a card discards it ---
    if (phase === "discard") {
      this._onDiscardClicked({ cardUid });
      return;
    }

    if (phase !== "play") return;

    // --- PLAY PHASE ---
    // Clicking the already-selected card → deselect.
    if (ui.selectedCardUid === cardUid) {
      this._store.clearSelection();
      return;
    }

    // Select the card.
    this._store.selectCard(cardUid);

    // If card needs no target, send immediately.
    if (NO_TARGET_CARD_IDS.has(cardId)) {
      this._playSelectedCard(null);
    } else {
      // Prompt user to pick a target.
      bus.emit("render:prompt_target", {
        message: "Select a target player",
        cardId,
      });
    }
  }

  _onTargetClicked({ playerId }) {
    const { ui } = this._store.state;

    if (!ui.selectedCardUid) return;  // no card selected, ignore
    if (!this._store.isMyTurn) return;
    if (this._store.currentTurnPhase !== "play") return;

    this._playSelectedCard(playerId);
  }

  _playSelectedCard(targetId) {
    const { ui } = this._store.state;
    const cardUid = ui.selectedCardUid;
    if (!cardUid) return;

    this._store.clearSelection();
    this._store.setPendingAction("play_card");
    this._socket.sendPlayCard(cardUid, targetId);
  }

  // ------------------------------------------------------------------
  // Discard phase
  // ------------------------------------------------------------------

  _onDiscardClicked({ cardUid }) {
    if (!this._store.isMyTurn) return;
    if (this._store.currentTurnPhase !== "discard") return;
    if (!cardUid) return;

    this._store.setPendingAction("discard");
    this._socket.sendDiscardCard(cardUid);
  }

  // ------------------------------------------------------------------
  // End turn
  // ------------------------------------------------------------------

  _onEndTurnClicked() {
    if (!this._store.isMyTurn) return;
    const phase = this._store.currentTurnPhase;
    if (phase !== "play" && phase !== "discard") return;

    this._store.setPendingAction("end_turn");
    this._socket.sendEndTurn();
  }

  // ------------------------------------------------------------------
  // Reaction (Missed!)
  // ------------------------------------------------------------------

  _onSkipReaction() {
    // Player chose not to react — nothing to send, server timer resolves it.
    // In future: if implementing explicit "play Missed!" reaction,
    //   this._socket.sendPlayCard(missedCardUid);
  }

  // ------------------------------------------------------------------
  // Chat
  // ------------------------------------------------------------------

  _onChatSubmitted({ message }) {
    const trimmed = message?.trim();
    if (!trimmed || trimmed.length > 256) return;
    this._socket.sendChat(trimmed);
  }

  // ------------------------------------------------------------------
  // Utility
  // ------------------------------------------------------------------

  _onPlayAgain() {
    // Reload to lobby — simplest "play again" flow.
    window.location.reload();
  }

  _onRequestSync() {
    this._socket.sendRequestSnapshot();
  }

  /**
   * Guard: can the local player take an action during this phase?
   *
   * @param {string} requiredPhase  "draw" | "play" | "discard"
   */
  _canAct(requiredPhase) {
    if (!this._store.isMyTurn) return false;
    if (this._store.currentTurnPhase !== requiredPhase) return false;
    if (this._store.state.ui.pendingAction) return false;
    return true;
  }
}
