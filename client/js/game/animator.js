/**
 * client/js/game/animator.js
 *
 * Animator — progressive-enhancement animation layer.
 *
 * DESIGN RULES:
 *   1. SUBSCRIBES TO render:* EVENTS — never to store:* or server:* directly.
 *      The render pipeline fires first (DOM is already updated), then the
 *      animator runs transitions on top of the settled DOM.
 *
 *   2. PURELY ADDITIVE — if this whole file is deleted, the game still works.
 *      Animations are CSS classes added/removed; nothing here changes state.
 *
 *   3. NO LAYOUT READS DURING ANIMATION — we use CSS custom properties and
 *      class toggles only.  No getBoundingClientRect() during RAF callbacks
 *      (would force layout thrash).  Exception: FLIP animations in
 *      animateCardPlay() which need one read before and one after.
 *
 *   4. RESPECTS prefers-reduced-motion — every animation function checks
 *      this._reducedMotion and early-returns if true.
 *
 * CSS ANIMATION CONTRACT:
 *   Animations are defined purely in CSS (main.css) using these classes:
 *     .anim-damage-flash    — brief red flash on an opponent card
 *     .anim-heal-pulse      — brief green pulse on HP bar
 *     .anim-card-played     — card flies from hand area (via FLIP)
 *     .anim-card-draw       — card slides in from deck area
 *     .anim-turn-start      — glow on active player
 *     .anim-elimination     — fade + skull drop on eliminated player
 *   All use CSS @keyframes with duration vars (--anim-duration-fast, etc.)
 *   so the speed can be tuned in one place.
 *
 * FUTURE:
 *   Replace class-toggle animations with the Web Animations API
 *   (element.animate()) for more precise sequencing and Promise-based
 *   completion callbacks.  The subscribe pattern here doesn't need to change.
 */

import { bus } from "../core/events.js";

// Duration must match --anim-duration-fast in CSS (200ms).
const FAST_MS   = 200;
const MEDIUM_MS = 400;

export class Animator {
  /**
   * @param {Store} store  Read-only store access for positional queries
   */
  constructor(store) {
    this._store        = store;
    this._reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;

    // Re-check if the user changes the OS setting mid-session.
    window.matchMedia("(prefers-reduced-motion: reduce)")
      .addEventListener("change", (e) => {
        this._reducedMotion = e.matches;
      });

    this._subscribe();
  }

  // ------------------------------------------------------------------
  // Subscriptions (render:* events only)
  // ------------------------------------------------------------------

  _subscribe() {
    bus.on("render:hp_updated",      (d) => this._animateDamageOrHeal(d));
    bus.on("render:hand_rendered",   ()  => this._animateHandDeal());
    bus.on("render:turn_rendered",   (d) => this._animateTurnStart(d));
    bus.on("render:players_rendered",()  => this._animatePlayersIn());
    bus.on("render:game_rendered",   ()  => this._animateGameStart());

    // store:* events the animator watches directly for things the
    // renderer doesn't have a matching render: event for.
    bus.on("store:card_played",       (d) => this._animateCardPlayed(d));
    bus.on("store:player_eliminated", (d) => this._animateElimination(d));
    bus.on("store:damage_dealt",      (d) => this._animateDamageFlash(d));
    bus.on("store:player_healed",     (d) => this._animateHealFlash(d));
  }

  // ------------------------------------------------------------------
  // Game start
  // ------------------------------------------------------------------

  _animateGameStart() {
    if (this._reducedMotion) return;
    const table = document.getElementById("game-panel");
    if (!table) return;
    this._flashClass(table, "anim-game-start", MEDIUM_MS * 2);
  }

  // ------------------------------------------------------------------
  // Players dealt in
  // ------------------------------------------------------------------

  _animatePlayersIn() {
    if (this._reducedMotion) return;
    const opponents = document.querySelectorAll(".opponent");
    opponents.forEach((el, i) => {
      el.style.animationDelay = `${i * 80}ms`;
      this._flashClass(el, "anim-slide-in", MEDIUM_MS + i * 80);
    });
  }

  // ------------------------------------------------------------------
  // Hand deal
  // ------------------------------------------------------------------

  _animateHandDeal() {
    if (this._reducedMotion) return;
    const cards = document.querySelectorAll(".hand-card");
    cards.forEach((el, i) => {
      el.style.animationDelay = `${i * 60}ms`;
      this._flashClass(el, "anim-card-deal", MEDIUM_MS + i * 60);
    });
  }

  // ------------------------------------------------------------------
  // Card played (FLIP — read position before DOM update is too late here,
  // so we use a simpler "card leaves hand" pulse instead)
  // ------------------------------------------------------------------

  _animateCardPlayed({ playerId }) {
    if (this._reducedMotion) return;
    const myId = this._store.state.myPlayerId;

    if (playerId === myId) {
      // Animate discard area receiving the card.
      const discard = document.getElementById("discard-top");
      if (discard) this._flashClass(discard, "anim-card-arrive", MEDIUM_MS);
    } else {
      // Opponent played — briefly pulse their hand count.
      const oppNode = document.querySelector(
        `[data-player-id="${CSS.escape(playerId)}"] .opponent__hand-count`
      );
      if (oppNode) this._flashClass(oppNode, "anim-card-played", FAST_MS);
    }
  }

  // ------------------------------------------------------------------
  // Turn start
  // ------------------------------------------------------------------

  _animateTurnStart({ isMyTurn }) {
    if (this._reducedMotion) return;

    if (isMyTurn) {
      const hand = document.getElementById("hand-area");
      if (hand) this._flashClass(hand, "anim-turn-start", MEDIUM_MS);
    }

    // Glow on the active opponent.
    const active = document.querySelector(".opponent--active");
    if (active) this._flashClass(active, "anim-turn-start", MEDIUM_MS);
  }

  // ------------------------------------------------------------------
  // Damage and heal flashes (called from store: events for timing)
  // ------------------------------------------------------------------

  _animateDamageFlash({ targetId }) {
    if (this._reducedMotion) return;
    this._flashOnPlayer(targetId, "anim-damage-flash", FAST_MS);
  }

  _animateHealFlash({ playerId }) {
    if (this._reducedMotion) return;
    this._flashOnPlayer(playerId, "anim-heal-pulse", FAST_MS);
  }

  _animateDamageOrHeal({ playerId }) {
    // Called after hp bar DOM update — animate the bar width change.
    if (this._reducedMotion) return;
    const myId = this._store.state.myPlayerId;

    if (playerId === myId) {
      const bar = document.getElementById("my-hp-bar");
      if (bar) this._flashClass(bar, "anim-hp-change", FAST_MS);
    } else {
      const fill = document.querySelector(
        `[data-player-id="${CSS.escape(playerId)}"] .opponent__hp-fill`
      );
      if (fill) this._flashClass(fill, "anim-hp-change", FAST_MS);
    }
  }

  // ------------------------------------------------------------------
  // Player elimination
  // ------------------------------------------------------------------

  _animateElimination({ playerId }) {
    if (this._reducedMotion) return;
    const node = document.querySelector(
      `[data-player-id="${CSS.escape(playerId)}"]`
    );
    if (!node) return;

    // Fade out then leave the --dead state (renderer adds the class).
    this._flashClass(node, "anim-elimination", MEDIUM_MS * 3);
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  /**
   * Add a CSS class, then remove it after `durationMs` milliseconds.
   * Uses requestAnimationFrame to ensure the class is added in a new frame,
   * avoiding cases where adding and removing in the same frame has no effect.
   */
  _flashClass(el, className, durationMs) {
    if (!el) return;
    requestAnimationFrame(() => {
      el.classList.add(className);
      setTimeout(() => el.classList.remove(className), durationMs);
    });
  }

  /**
   * Flash a class on the DOM node for a specific player_id.
   * Works for both the local player and opponents.
   */
  _flashOnPlayer(playerId, className, durationMs) {
    const myId = this._store.state.myPlayerId;

    if (playerId === myId) {
      const el = document.getElementById("my-player-area");
      if (el) this._flashClass(el, className, durationMs);
      return;
    }

    const node = document.querySelector(
      `[data-player-id="${CSS.escape(playerId)}"]`
    );
    if (node) this._flashClass(node, className, durationMs);
  }
}
