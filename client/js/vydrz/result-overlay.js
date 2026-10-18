/**
 * client/js/vydrz/result-overlay.js
 *
 * The round "zúčtování": replays the server's scoring result with Balatro-
 * style feedback. For each drawn drink card the stats are walked in a fixed
 * order (postava, lokace, existence); whenever a card has that drink, the
 * player's running number changes, pops up, and a sound plays.
 *
 * PRESENTATION ONLY. Every number shown comes from the server's result
 * payload (VydrzGame._resolve_round). This file adds animation and never
 * computes who wins.
 *
 * Skipping: the "Přeskočit" button (and prefers-reduced-motion) jumps to the
 * final state, which is rendered by the same code with zero delays.
 */

import { drinkFront, cardBack, drinkInfo, fmtStat } from "./cards.js";
import * as sfx from "./sound.js";
import { showBaronSplash } from "./winner-splash.js";
import { getSettings, SPEED_FACTOR } from "./settings.js";

const SOURCES = ["postava", "lokace", "existence"];
const SOURCE_LABEL = { postava: "Post.", lokace: "Lok.", existence: "Exist." };

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const cls = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "zero");

export class ResultOverlay {
  /**
   * @param {HTMLElement} root
   * @param {{ onNext: () => void, onLeave: () => void }} handlers
   */
  constructor(root, { onNext, onLeave }) {
    this._root = root;
    this._onNext = onNext;
    this._onLeave = onLeave;
    this._token = 0;          // cancels a running animation when a new one starts
    this._skip = false;
    this.isOpen = false;
  }

  close() {
    this._token++;
    this.isOpen = false;
    this._root.hidden = true;
    this._root.innerHTML = "";
  }

  /**
   * @param {object} result   VydrzGame.last_result
   * @param {{ names: Record<string,string>, isHost: boolean, myId: string }} ctx
   * @param {{ animate?: boolean }} opts
   */
  async open(result, ctx, { animate = true } = {}) {
    const token = ++this._token;
    this.isOpen = true;
    this._skip = !animate || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    this._build(result, ctx);
    this._root.hidden = false;

    // Counting speed (settings): every pause is stretched, and so are the pops.
    const factor = SPEED_FACTOR[getSettings().speed - 1] ?? 1;
    this._root.style.setProperty("--vr-speed", String(factor));
    const wait = (ms) => new Promise((res) => {
      if (this._skip || ms <= 0) return res();
      setTimeout(res, ms * factor);
    });
    const alive = () => token === this._token;

    this._q(".vr__skip").addEventListener("click", () => {
      this._skip = true;
      this._q(".vr__skip").hidden = true;
    });

    const totals = Object.fromEntries(Object.keys(result.totals).map(pid => [pid, 0]));

    // ---- walk the three drawn drinks -------------------------------------
    for (let i = 0; i < result.drawn.length; i++) {
      const row = result.drawn[i];
      this._flipSlot(i, row.drink);
      this._note(`${drinkInfo(row.drink).name}: sčítáme staty…`);
      this._clearCells();
      await wait(520); if (!alive()) return;

      for (const src of SOURCES) {
        this._markColumn(src);
        let maxPos = 0, anyNeg = false;
        for (const [pid, c] of Object.entries(row.players)) {
          const v = c[src];
          this._setCell(pid, src, v);
          if (v) {
            totals[pid] += v;
            this._setTotal(pid, totals[pid], v);
            if (v > 0) maxPos = Math.max(maxPos, v); else anyNeg = true;
          }
        }
        const lead = Math.max(...Object.values(totals));
        if (maxPos) sfx.tick(maxPos, lead);
        if (anyNeg) sfx.tick(-1, lead);
        await wait(430); if (!alive()) return;
      }
      this._markColumn(null);
      await wait(380); if (!alive()) return;
    }
    this._clearCells();
    this._q("[data-table]").classList.add("is-done");     // final totals only
    this._note("Součty hotové.");
    this._highlightLeaders(result);
    await wait(500); if (!alive()) return;

    // ---- tie-break draws ---------------------------------------------------
    if (result.tiebreak.length) {
      this._note("Remíza! Rozstřel: počítají se jen postavy.");
      sfx.tieSting();
      await wait(700); if (!alive()) return;
      for (const step of result.tiebreak) {
        await this._playTieStep(step, ctx, wait, alive);
        if (!alive()) return;
      }
    }

    // ---- outcome -----------------------------------------------------------
    this._showOutcome(result, ctx);
    await this._countScores(result, wait, alive);
    if (!alive()) return;
    if (result.final) {
      // Everybody sees who is the Baron before the final table.
      const f = result.final;
      await showBaronSplash({ name: f.winner_id ? (ctx.names[f.winner_id] ?? f.winner_id) : null, isMe: f.winner_id === ctx.myId });
      if (!alive()) return;
      this._showFinal(f, ctx);
    }
    this._showFooter(result, ctx);
  }

  // ---------------------------------------------------------------- building

  _build(result, ctx) {
    const rows = Object.keys(result.totals).map((pid) => {
      const pick = result.picks[pid];
      return `<div class="vr__row" data-pid="${esc(pid)}">
        <div class="vr__who"><b>${esc(ctx.names[pid] ?? pid)}</b>
          <small>${esc(pick.lokace.name)} · ${esc(pick.existence.name)}</small></div>
        ${SOURCES.map(s => `<div class="vr__cell" data-src="${s}">·</div>`).join("")}
        <div class="vr__total" data-total>0</div>
      </div>`;
    }).join("");

    this._root.innerHTML = `
      <div class="vr__backdrop"></div>
      <div class="vr__panel" role="document">
        <header class="vr__head">
          <h2 id="vr-title">Kolo ${result.round} · Zúčtování</h2>
          <button class="btn btn--ghost btn--small vr__skip" type="button">Přeskočit</button>
          <button class="hud__btn vr__close" type="button" aria-label="Skrýt výsledek">✕</button>
        </header>
        <div class="vr__drinks">
          ${result.drawn.map((_, i) => `<div class="vr__slot" data-i="${i}">${cardBack("chlast")}</div>`).join("")}
        </div>
        <p class="vr__note" aria-live="polite"></p>
        <div class="vr__table" data-table>
          <div class="vr__cols"><span></span>${SOURCES.map(s => `<span data-col="${s}">${SOURCE_LABEL[s]}</span>`).join("")}<span>Σ</span></div>
          ${rows}
        </div>
        <div class="vr__tie" hidden></div>
        <div class="vr__outcome" hidden></div>
        <div class="vr__scores" hidden></div>
        <div class="vr__final" hidden></div>
        <footer class="vr__foot" hidden></footer>
      </div>`;
    this._root.setAttribute("aria-labelledby", "vr-title");
    this._q(".vr__close").addEventListener("click", () => this.close());
    this._q(".vr__backdrop").addEventListener("click", () => this.close());
  }

  _q(sel) { return this._root.querySelector(sel); }
  _row(pid) { return this._root.querySelector(`.vr__row[data-pid="${CSS.escape(pid)}"]`); }
  _note(text) { const n = this._q(".vr__note"); if (n) n.textContent = text; }

  _flipSlot(i, drink) {
    const slot = this._q(`.vr__slot[data-i="${i}"]`);
    if (!slot) return;
    slot.innerHTML = drinkFront(drink);
    slot.classList.remove("is-flipped"); void slot.offsetWidth; slot.classList.add("is-flipped");
    this._root.querySelectorAll(".vr__slot").forEach(s => s.classList.toggle("is-current", s === slot));
    sfx.flip();
  }

  _clearCells() {
    this._root.querySelectorAll(".vr__cell").forEach((c) => {
      c.textContent = "·"; c.className = "vr__cell";
    });
  }

  _markColumn(src) {
    this._root.querySelectorAll("[data-col]").forEach(h => h.classList.toggle("is-active", h.dataset.col === src));
  }

  _setCell(pid, src, v) {
    const cell = this._row(pid)?.querySelector(`.vr__cell[data-src="${src}"]`);
    if (!cell) return;
    cell.textContent = v == null ? "—" : fmtStat(v);
    cell.className = `vr__cell ${v ? `vr__cell--${cls(v)} is-hit` : "is-miss"}`;
  }

  _setTotal(pid, value, delta) {
    const el = this._row(pid)?.querySelector("[data-total]");
    if (!el) return;
    el.textContent = String(value).replace("-", "−");
    el.classList.remove("bump-pos", "bump-neg");
    void el.offsetWidth;                       // restart the animation
    el.classList.add(delta > 0 ? "bump-pos" : "bump-neg");
  }

  _highlightLeaders(result) {
    const best = Math.max(...Object.values(result.totals));
    for (const [pid, total] of Object.entries(result.totals)) {
      this._row(pid)?.classList.toggle("is-lead", total === best);
    }
  }

  async _playTieStep(step, ctx, wait, alive) {
    const box = this._q(".vr__tie");
    box.hidden = false;
    const el = document.createElement("div");
    el.className = "vr__tiestep";
    el.innerHTML = `<div class="vr__tiecard">${drinkFront(step.drink)}</div>
      <div class="vr__tievals">${Object.entries(step.values).map(([pid, v]) =>
        `<span class="vr__tieval vr__tieval--${cls(v)}${step.remaining.includes(pid) ? " is-in" : " is-out"}" data-pid="${esc(pid)}">
           ${esc(ctx.names[pid] ?? pid)} <b>${fmtStat(v)}</b></span>`).join("")}</div>`;
    box.appendChild(el);
    sfx.flip();
    await wait(450); if (!alive()) return;
    el.classList.add("is-resolved");
    sfx.tick(Math.max(...Object.values(step.values)), 0);
    await wait(750);
  }

  _showOutcome(result, ctx) {
    const out = this._q(".vr__outcome");
    const w = result.winner_id;
    this._root.querySelectorAll(".vr__row").forEach((r) => {
      r.classList.toggle("is-winner", r.dataset.pid === w);
      r.classList.toggle("is-out", !!w && r.dataset.pid !== w);
    });
    out.hidden = false;
    if (w) {
      const loc = result.picks[w].lokace;
      out.innerHTML = `<b>${esc(ctx.names[w] ?? w)}</b> bere lokaci <b>${esc(loc.name)}</b> a získává <b class="vr__plus">+${result.awarded}</b> b.`;
      sfx.fanfare();
    } else {
      out.textContent = "Remíza se nepodařilo rozhodnout. Lokaci nikdo nezíská.";
    }
    this._note("");
  }

  async _countScores(result, wait, alive) {
    const box = this._q(".vr__scores");
    box.hidden = false;
    box.innerHTML = Object.keys(result.scores_after).map((pid) =>
      `<div class="vr__score" data-pid="${esc(pid)}"><span>${esc(this._name(pid))}</span><b>${result.scores_before[pid]}</b></div>`
    ).join("");
    await wait(450); if (!alive()) return;
    for (const pid of Object.keys(result.scores_after)) {
      const el = box.querySelector(`[data-pid="${CSS.escape(pid)}"] b`);
      const from = result.scores_before[pid], to = result.scores_after[pid];
      el.textContent = String(to);
      if (to !== from) { el.classList.add("bump-pos"); }
    }
  }

  _name(pid) {
    return this._row(pid)?.querySelector(".vr__who b")?.textContent ?? pid;
  }

  _showFinal(final, ctx) {
    const box = this._q(".vr__final");
    box.hidden = false;
    const label = (pid) => (pid === final.winner_id ? "Vítěz" : pid === final.loser_id ? "Poražený" : "");
    const steps = (list, title) => list.length
      ? `<p class="vr__finalnote">${title}: ${list.map(s => `${esc(drinkInfo(s.drink).name)} (${Object.entries(s.values)
          .map(([pid, v]) => `${esc(ctx.names[pid] ?? pid)} ${fmtStat(v)}`).join(", ")})`).join(" → ")}</p>` : "";
    box.innerHTML = `<h3>Konec hry</h3>
      <ol class="vr__ranking">${final.ranking.map(r =>
        `<li class="${r.player_id === final.winner_id ? "is-winner" : r.player_id === final.loser_id ? "is-loser" : ""}">
          <span class="vr__place">${r.place}.</span><span class="vr__rname">${esc(ctx.names[r.player_id] ?? r.player_id)}</span>
          <b>${r.score} b.</b><em>${label(r.player_id)}</em></li>`).join("")}</ol>
      ${steps(final.tiebreak_first, "Rozstřel o 1. místo")}${steps(final.tiebreak_last, "Rozstřel o poslední místo")}`;
    sfx.fanfare();
  }

  _showFooter(result, ctx) {
    const foot = this._q(".vr__foot");
    foot.hidden = false;
    this._q(".vr__skip").hidden = true;
    if (result.final) {
      foot.innerHTML = `<button class="btn btn--block vr__leave" type="button">Opustit hru</button>`;
      foot.querySelector(".vr__leave").addEventListener("click", () => this._onLeave());
    } else if (ctx.isHost) {
      foot.innerHTML = `<button class="btn btn--block vr__next" type="button">Další kolo</button>`;
      foot.querySelector(".vr__next").addEventListener("click", () => { sfx.unlockAudio(); this._onNext(); });
    } else {
      foot.innerHTML = `<p class="vr__waiting">Čeká se, až host spustí další kolo…</p>`;
    }
  }
}
