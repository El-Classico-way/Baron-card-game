/**
 * client/js/game/renderer.js
 *
 * Rendering pipeline — translates store state into DOM.
 *
 * DESIGN RULES:
 *   1. PURE OUTPUT — renderer reads store state and updates DOM.
 *      It never modifies store state and never sends to the server.
 *
 *   2. EVENT-DRIVEN — every render function is triggered by a specific
 *      "store:*" event.  No polling, no RAF loops, no timers here.
 *      (Animations live in animator.js which uses RAF.)
 *
 *   3. TARGETED UPDATES — each store event triggers the smallest
 *      possible DOM update.  "store:hand_updated" re-renders only the
 *      hand panel.  "store:damage_dealt" updates only the HP bar for
 *      one player.  Full re-renders are reserved for state_replaced.
 *
 *   4. IDEMPOTENT — calling any render function twice with the same
 *      state produces the same DOM.  Safe to call redundantly.
 *
 *   5. NO REFERENCES TO SOCKET — renderer doesn't know about the
 *      network.  User interactions emit "ui:*" events; the Controller
 *      (controller.js) listens to those and calls socket methods.
 *
 * ANIMATION HOOKS:
 *   Render functions emit "render:*" events AFTER updating the DOM.
 *   The animator subscribes to those to layer CSS transitions on top.
 *   This keeps animation entirely opt-in and removable.
 */

import { bus } from "../core/events.js";
import { CardView } from "../ui/card-view.js";

export class Renderer {
  /**
   * @param {Store}  store
   * @param {object} elements  Pre-queried DOM element references
   */
  constructor(store, elements) {
    this._store = store;
    this._el    = elements;
    this._subscribe();
  }

  // ------------------------------------------------------------------
  // Subscriptions
  // ------------------------------------------------------------------

  _subscribe() {
    bus.on("store:joined",          ()  => this.renderLobby());
    bus.on("store:lobby_updated",   ()  => this.renderLobby());
    bus.on("store:state_replaced",  ()  => this.renderGame());
    bus.on("store:hand_updated",    ()  => this.renderHand());
    bus.on("store:turn_changed",    ()  => {
      this.renderTurnIndicator();
      this.renderActionBar();   // buttons depend on whose turn it is
      this.renderHand();        // hand cards are only clickable on my turn
    });
    bus.on("store:phase_changed",   ()  => {
      this.renderActionBar();
      this.renderTurnIndicator();
    });
    bus.on("store:selection_changed",() => this.renderHand());
    bus.on("store:damage_dealt",    (d) => this.renderPlayerHp(d.targetId));
    bus.on("store:player_healed",   (d) => this.renderPlayerHp(d.playerId));
    bus.on("store:player_eliminated",(d)=> this.renderPlayerStatus(d.playerId));
    bus.on("store:card_played",     (d) => this.renderOpponentHandCount(d.playerId));
    bus.on("store:card_drawn",      (d) => this.renderOpponentHandCount(d.playerId));
    bus.on("store:card_discarded",  (d) => this.renderOpponentHandCount(d.playerId));
    bus.on("store:game_ended",      (d) => this.renderGameOver(d));
    bus.on("store:notification",    (n) => this.renderNotification(n));
    bus.on("store:error",           (d) => this.renderError(d.message));
    bus.on("store:chat_received",   ()  => this.renderChat());
    bus.on("store:connection_changed",(s)=> this.renderConnectionStatus(s));
    bus.on("store:reaction_window_open",(d)=> this.renderReactionWindow(d));

    // Pending action state → action bar button disabled state
    bus.on("store:pending_action_changed", () => this.renderActionBar());
    bus.on("store:action_acked",           () => this.renderActionBar());

    // cards_dealt fires before private_hand — update opponent hand counts
    bus.on("store:cards_dealt", ({ counts }) => {
      if (!this._store.state.game) return;
      for (const playerId of Object.keys(counts)) {
        this.renderOpponentHandCount(playerId);
      }
    });

    // Turn ended — clear active highlight, re-render action bar
    bus.on("store:turn_ended", () => {
      this.renderActionBar();
      this.renderTurnIndicator();
    });

    // Error dismissed → clear any inline error display
    bus.on("store:error_cleared", () => {
      if (this._el.errorDisplay) this._el.errorDisplay.textContent = "";
    });

    // Target prompt — show message above hand asking player to pick a target
    bus.on("render:prompt_target", ({ message }) => {
      this._showTargetPrompt(message);
    });

    // Selection cleared → hide target prompt
    bus.on("store:selection_changed", ({ cardUid }) => {
      if (!cardUid) this._hideTargetPrompt();
    });
  }

  // ------------------------------------------------------------------
  // Target prompt helpers
  // ------------------------------------------------------------------

  _showTargetPrompt(message) {
    let el = document.getElementById("target-prompt");
    if (!el) {
      el = document.createElement("div");
      el.id = "target-prompt";
      el.className = "target-prompt";
      // Insert above the action bar if it exists, else append to body
      const actionBar = this._el.actionBar;
      if (actionBar?.parentNode) {
        actionBar.parentNode.insertBefore(el, actionBar);
      } else {
        document.body.appendChild(el);
      }
    }
    el.textContent = message;
    el.hidden = false;
    document.body.classList.add("is-targeting");
  }

  _hideTargetPrompt() {
    const el = document.getElementById("target-prompt");
    if (el) el.hidden = true;
    document.body.classList.remove("is-targeting");
  }

  // ------------------------------------------------------------------
  // Lobby
  // ------------------------------------------------------------------

  renderLobby() {
    const { lobby, myPlayerId, roomId } = this._store.state;
    const { el } = this;

    if (el.roomIdDisplay)   el.roomIdDisplay.textContent  = roomId ?? "";
    if (el.lobbyPlayerList) {
      el.lobbyPlayerList.innerHTML = lobby.players.map(p => `
        <li class="lobby-player ${p.connected === false ? "disconnected" : ""}"
            data-player-id="${p.player_id}"
            data-initial="${_esc((p.display_name ?? "?").trim().charAt(0).toUpperCase())}">
          <span class="lobby-player__name">${_esc(p.display_name)}</span>
          ${p.player_id === lobby.hostPlayerId
            ? '<span class="lobby-player__badge">host</span>' : ""}
          ${p.player_id === myPlayerId
            ? '<span class="lobby-player__badge lobby-player__badge--you">ty</span>' : ""}
        </li>
      `).join("");
    }

    this._renderModeSwitch();

    // Show/hide start button for host.
    if (el.startGameBtn) {
      const isHost = myPlayerId === lobby.hostPlayerId;
      const enoughPlayers = lobby.players.length >= 2;
      el.startGameBtn.hidden   = !isHost;
      el.startGameBtn.disabled = !enoughPlayers;
      const hint = document.getElementById("lobby-hint");
      if (hint) {
        hint.textContent = !isHost
          ? "Čeká se, až host spustí hru…"
          : enoughPlayers ? "Jsou všichni? Začni, kdykoli budete připraveni."
                          : "Čeká se aspoň na jednoho dalšího hráče…";
      }
    }
  }

  // ------------------------------------------------------------------
  // Full game render (on state_replaced)
  // ------------------------------------------------------------------

  renderGame() {
    const { game } = this._store.state;
    if (!game) return;

    this._hideTargetPrompt();   // a resync clears any half-finished selection
    this.renderAllPlayers();
    this.renderHand();
    this.renderDeck();
    this.renderTurnIndicator();
    this.renderActionBar();
    this.renderRolePanel();

    // Switch panels
    this._showPanel("game");
    bus.emit("render:game_rendered", {});
  }

  // ------------------------------------------------------------------
  // Players area (all opponents in a ring)
  // ------------------------------------------------------------------

  renderAllPlayers() {
    const { game, myPlayerId } = this._store.state;
    if (!game) return;

    const el = this._el.opponentsArea;
    if (!el) return;

    const opponents = game.players.filter(p => p.player_id !== myPlayerId);
    el.innerHTML = opponents.map(p => this._buildOpponentCard(p)).join("");

    // Bind click-to-target on opponent cards.
    el.querySelectorAll(".opponent[data-player-id]").forEach(node => {
      node.addEventListener("click", () => {
        bus.emit("ui:target_clicked", { playerId: node.dataset.playerId });
      });
    });

    // Own HP lives outside the opponents ring; draw it on every full render.
    this.renderPlayerHp(myPlayerId);

    bus.emit("render:players_rendered", {});
  }

  _buildOpponentCard(player) {
    const hpPct = Math.max(0, (player.hp / player.max_hp) * 100);
    const dead   = player.status === "dead";
    const equipment = player.equipment ?? [];

    return `
      <div class="opponent ${dead ? "opponent--dead" : ""}"
           data-player-id="${player.player_id}"
           aria-label="${_esc(player.display_name)}">
        <div class="opponent__name">${_esc(player.display_name)}</div>
        ${player.role ? `<div class="opponent__role opponent__role--${player.role}">${player.role}</div>` : ""}
        <div class="opponent__hp-bar" aria-label="${player.hp}/${player.max_hp} HP">
          <div class="opponent__hp-fill" style="width:${hpPct}%"></div>
        </div>
        <div class="opponent__meta">
          <span class="opponent__hp-label">${player.hp}/${player.max_hp}</span>
          <span class="opponent__hand-count" aria-label="${player.hand_count ?? 0} karet v ruce">${player.hand_count ?? 0}</span>
        </div>
        ${equipment.length > 0 ? `
          <div class="opponent__equipment">
            ${equipment.map(c => `
              <span class="equipment-badge equipment-badge--${c.id}" title="${_esc(c.name)}">
                ${_esc(c.name)}
              </span>
            `).join("")}
          </div>
        ` : ""}
        ${dead ? '<div class="opponent__dead-overlay">✞</div>' : ""}
      </div>
    `;
  }

  // ------------------------------------------------------------------
  // Individual player patches (avoid full re-render for single events)
  // ------------------------------------------------------------------

  renderPlayerHp(playerId) {
    const { game } = this._store.state;
    if (!game) return;

    const player = game.players.find(p => p.player_id === playerId);
    if (!player) return;

    // Local player HP
    if (playerId === this._store.state.myPlayerId) {
      const el = this._el.myHpDisplay;
      if (el) el.textContent = `${player.hp}/${player.max_hp}`;
      // Target the fill child div, not the bar container
      const fill = document.getElementById("my-hp-bar-fill");
      if (fill) fill.style.width = `${(player.hp / player.max_hp) * 100}%`;
      return;
    }

    // Opponent HP (patch only the HP elements for this opponent)
    const node = this._el.opponentsArea?.querySelector(
      `[data-player-id="${CSS.escape(playerId)}"]`
    );
    if (!node) return;

    const fill  = node.querySelector(".opponent__hp-fill");
    const label = node.querySelector(".opponent__hp-label");
    if (fill)  fill.style.width   = `${(player.hp / player.max_hp) * 100}%`;
    if (label) label.textContent  = `${player.hp}/${player.max_hp}`;

    bus.emit("render:hp_updated", { playerId, hp: player.hp, maxHp: player.max_hp });
  }

  renderPlayerStatus(playerId) {
    const node = this._el.opponentsArea?.querySelector(
      `[data-player-id="${CSS.escape(playerId)}"]`
    );
    if (node) {
      node.classList.add("opponent--dead");
      const { game } = this._store.state;
      const player = game?.players.find(p => p.player_id === playerId);
      if (player?.role) {
        const roleEl = node.querySelector(".opponent__role");
        if (roleEl) {
          roleEl.textContent = player.role;
          roleEl.className   = `opponent__role opponent__role--${player.role}`;
        }
      }
    }
  }

  /**
   * Game mode (family) and, for games with variants, how scoring is counted.
   * Only the host can change them.
   */
  _renderModeSwitch() {
    const box = document.getElementById("mode-switch");
    if (!box) return;
    const { lobby, myPlayerId } = this._store.state;
    const isHost = myPlayerId === lobby.hostPlayerId;
    const modes = lobby.modes ?? [];
    const current = modes.find(m => m.id === lobby.mode);
    const families = [...new Set(modes.map(m => m.family ?? m.id))];

    box.innerHTML = families.map((fam) => {
      const members = modes.filter(m => (m.family ?? m.id) === fam);
      const playable = members.some(m => m.playable);
      const on = (current?.family ?? current?.id) === fam;
      const target = members.find(m => m.id === lobby.mode) ?? members[0];
      return `<button class="seg__btn${on ? " is-on" : ""}" type="button" role="radio" aria-checked="${on}"
                data-mode="${_esc(target.id)}" ${!playable || !isHost ? "disabled" : ""}>${_esc(members[0].name)}${playable ? "" : " <small>(brzy)</small>"}</button>`;
    }).join("");

    const variants = modes.filter(m => (m.family ?? m.id) === (current?.family ?? current?.id) && m.variant);
    const vbox = document.getElementById("variant-box");
    if (vbox) vbox.hidden = variants.length < 2;
    const vs = document.getElementById("variant-switch");
    if (vs) {
      vs.innerHTML = variants.map(m => `<button class="seg__btn${m.id === lobby.mode ? " is-on" : ""}" type="button" role="radio"
          aria-checked="${m.id === lobby.mode}" data-mode="${_esc(m.id)}" ${!isHost ? "disabled" : ""}>${_esc(m.variant)}</button>`).join("");
    }
    const hint = document.getElementById("variant-hint");
    if (hint) hint.textContent = lobby.mode === "vydrz_rucne"
      ? "Všech 8 dvojic si naplánuješ předem a každé kolo si čísla spočítáš sám(a)."
      : "Hra čísla spočítá sama, s animací.";
  }

  renderOpponentHandCount(playerId) {
    if (playerId === this._store.state.myPlayerId) return;
    const { game } = this._store.state;
    if (!game) return;

    const player = game.players.find(p => p.player_id === playerId);
    if (!player) return;

    const node = this._el.opponentsArea?.querySelector(
      `[data-player-id="${CSS.escape(playerId)}"]`
    );
    if (!node) return;

    const countEl = node.querySelector(".opponent__hand-count");
    if (countEl) {
      const n = player.hand_count ?? 0;
      countEl.textContent = String(n);
    }
  }

  // ------------------------------------------------------------------
  // Local player hand
  // ------------------------------------------------------------------

  renderHand() {
    const el = this._el.handArea;
    if (!el) return;

    const me = this._store.myPlayer;
    if (!me?.hand) {
      el.innerHTML = "";
      return;
    }

    const selectedUid  = this._store.state.ui.selectedCardUid;
    const isMyTurn     = this._store.isMyTurn;
    const phase        = this._store.currentTurnPhase;
    const canPlay      = isMyTurn && (phase === "play" || phase === "discard");

    el.innerHTML = me.hand.map(card => {
      const isSelected = card.uid === selectedUid;
      const isPlayable = canPlay;
      return CardView.buildHandCardHTML(card, { isSelected, isPlayable });
    }).join("");

    // Bind card click events.
    el.querySelectorAll(".hand-card[data-uid]").forEach(node => {
      node.addEventListener("click", () => {
        bus.emit("ui:card_clicked", {
          cardUid: node.dataset.uid,
          cardId:  node.dataset.id,
        });
      });
    });

    bus.emit("render:hand_rendered", { count: me.hand.length });
  }

  // ------------------------------------------------------------------
  // Deck display
  // ------------------------------------------------------------------

  renderDeck() {
    const { game } = this._store.state;
    if (!game) return;

    if (this._el.deckCount)    this._el.deckCount.textContent    = game.deck.drawCount;
    if (this._el.discardCount) this._el.discardCount.textContent = game.deck.discardCount;

    if (this._el.discardTop) {
      const top = game.deck.topDiscard;
      if (top) {
        this._el.discardTop.innerHTML  = CardView.buildDiscardHTML(top);
        this._el.discardTop.classList.remove("discard-pile--empty");
      } else {
        this._el.discardTop.innerHTML  = "";
        this._el.discardTop.classList.add("discard-pile--empty");
      }
    }
  }

  // ------------------------------------------------------------------
  // Turn indicator
  // ------------------------------------------------------------------

  renderTurnIndicator() {
    const { game, myPlayerId } = this._store.state;
    if (!game?.turn) return;

    const currentPlayer = game.players.find(p => p.player_id === game.turn.playerId);
    const isMyTurn      = game.turn.playerId === myPlayerId;

    if (this._el.turnIndicator) {
      const phase = game.turn.phase;
      const suffix = isMyTurn && phase === "discard" ? " – odhoď karty na počet životů" : "";
      this._el.turnIndicator.textContent = isMyTurn
        ? `Tvůj tah${suffix}`
        : `Hraje ${currentPlayer?.display_name ?? "?"}`;
      this._el.turnIndicator.dataset.phase = phase;
      this._el.turnIndicator.classList.toggle("turn-indicator--mine", isMyTurn);
    }

    // Highlight whose turn it is in the opponents ring.
    this._el.opponentsArea?.querySelectorAll(".opponent").forEach(node => {
      node.classList.toggle(
        "opponent--active",
        node.dataset.playerId === game.turn.playerId
      );
    });

    bus.emit("render:turn_rendered", { isMyTurn });
  }

  // ------------------------------------------------------------------
  // Action bar (buttons below hand)
  // ------------------------------------------------------------------

  renderActionBar() {
    const el = this._el.actionBar;
    if (!el) return;

    const isMyTurn = this._store.isMyTurn;
    const phase    = this._store.currentTurnPhase;
    const pending  = this._store.state.ui.pendingAction;

    const show = (selector, visible) => {
      const btn = el.querySelector(selector);
      if (btn) btn.hidden = !visible;
      if (btn) btn.disabled = !!pending;
    };

    show("[data-action='draw']",    isMyTurn && phase === "draw");
    show("[data-action='end-turn']",isMyTurn && (phase === "play" || phase === "discard"));
    show("[data-action='discard']", false);  // card click handles discard target

    // Dim action bar if it's not our turn.
    el.classList.toggle("action-bar--inactive", !isMyTurn);
  }

  // ------------------------------------------------------------------
  // Role panel (shown once game starts)
  // ------------------------------------------------------------------

  renderRolePanel() {
    const el = this._el.rolePanel;
    if (!el) return;

    const { myRole } = this._store.state;
    if (!myRole) return;

    el.innerHTML = `
      <div class="role-card role-card--${myRole}">
        <div class="role-card__label">Your role</div>
        <div class="role-card__name">${myRole}</div>
        <div class="role-card__desc">${_roleDescription(myRole)}</div>
      </div>
    `;
    el.hidden = false;
  }

  // ------------------------------------------------------------------
  // Game over overlay
  // ------------------------------------------------------------------

  renderGameOver({ winnerIds, reason }) {
    const el = this._el.gameOverlay;
    if (!el) return;

    const { game, myPlayerId } = this._store.state;
    const iWon = winnerIds.includes(myPlayerId);
    const winnerNames = (game?.players ?? [])
      .filter(p => winnerIds.includes(p.player_id))
      .map(p => p.display_name)
      .join(", ");

    el.innerHTML = `
      <div class="game-over ${iWon ? "game-over--win" : "game-over--lose"}">
        <h2 class="game-over__title">${iWon ? "Vítězství!" : "Porážka"}</h2>
        <p class="game-over__winners">Vyhrává: ${_esc(winnerNames)}</p>
        <p class="game-over__reason">${_esc(reason ?? "")}</p>
        <button class="btn" id="btn-play-again">Hrát znovu</button>
      </div>
    `;
    el.hidden = false;

    el.querySelector("#btn-play-again")?.addEventListener("click", () => {
      bus.emit("ui:play_again_clicked", {});
    });
  }

  // ------------------------------------------------------------------
  // Notifications and errors
  // ------------------------------------------------------------------

  renderNotification({ message, type, id }) {
    const el = this._el.notificationArea;
    if (!el) return;

    const div = document.createElement("div");
    div.className = `notification notification--${type}`;
    div.dataset.id = id;
    div.textContent = message;
    el.appendChild(div);

    // Auto-dismiss after 3s.
    setTimeout(() => div.remove(), 3000);
  }

  renderError(message) {
    if (!message) return;
    this.renderNotification({ message, type: "error", id: Date.now() });
  }

  // ------------------------------------------------------------------
  // Reaction window
  // ------------------------------------------------------------------

  renderReactionWindow({ triggerCardId, timeoutSeconds }) {
    const el = this._el.reactionOverlay;
    if (!el) return;

    el.innerHTML = `
      <div class="reaction-window">
        <p>Zahraj <strong>Missed!</strong> a uhni?</p>
        <div class="reaction-window__timer" id="reaction-timer">${timeoutSeconds}</div>
        <button class="btn btn--danger" id="btn-skip-reaction">Přijmout zásah</button>
      </div>
    `;
    el.hidden = false;

    // Countdown
    let remaining = timeoutSeconds;
    const tick = setInterval(() => {
      remaining--;
      const timerEl = el.querySelector("#reaction-timer");
      if (timerEl) timerEl.textContent = remaining;
      if (remaining <= 0) {
        clearInterval(tick);
        el.hidden = true;
      }
    }, 1000);

    el.querySelector("#btn-skip-reaction")?.addEventListener("click", () => {
      clearInterval(tick);
      el.hidden = true;
      bus.emit("ui:skip_reaction_clicked", {});
    });
  }

  // ------------------------------------------------------------------
  // Chat
  // ------------------------------------------------------------------

  renderChat() {
    const el = this._el.chatMessages;
    if (!el) return;

    const { chat } = this._store.state.ui;
    const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 4;

    // Append only new messages rather than rebuilding all.
    const existingIds = new Set(
      [...el.querySelectorAll("[data-ts]")].map(n => n.dataset.ts)
    );
    chat.forEach(msg => {
      const key = String(msg.timestamp);
      if (existingIds.has(key)) return;

      const div = document.createElement("div");
      div.className = "chat-message";
      div.dataset.ts = key;
      div.innerHTML = `
        <span class="chat-message__name">${_esc(msg.display_name)}</span>
        <span class="chat-message__text">${_esc(msg.message)}</span>
      `;
      el.appendChild(div);
    });

    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  // ------------------------------------------------------------------
  // Connection status banner
  // ------------------------------------------------------------------

  renderConnectionStatus(status) {
    const el = this._el.connectionBanner;
    if (!el) return;

    el.hidden = status === "connected";
    el.className = `connection-banner connection-banner--${status}`;
    el.textContent = {
      disconnected:  "Spojení přerušeno, zkouším se připojit znovu…",
      reconnecting:  "Připojuji znovu…",
    }[status] ?? "";
  }

  // ------------------------------------------------------------------
  // Panel switching
  // ------------------------------------------------------------------

  _showPanel(name) {
    ["lobby", "game", "join"].forEach(n => {
      const el = this._el[`${n}Panel`];
      if (el) el.hidden = n !== name;
    });
  }

  // ------------------------------------------------------------------
  // Shorthand for element access
  // ------------------------------------------------------------------

  get el() { return this._el; }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function _esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _roleDescription(role) {
  return {
    sheriff:  "Vyřaď všechny psance a odpadlíka. Tvoje životy jsou veřejné.",
    deputy:   "Chraň šerifa. Vyhráš, když jsou všichni nepřátelé vyřazeni.",
    outlaw:   "Vyřaď šerifa. Za každého vyřazeného líznu 3 karty.",
    renegade: "Přežij jako poslední. Vyřaď všechny, i psance.",
  }[role] ?? "";
}
