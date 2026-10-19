/**
 * client/js/vydrz/winner-splash.js
 *
 * End-of-game announcement shown to every player: "<Jméno> je Baron!".
 * Presentation only; the winner comes from the server's final result.
 * Tap, Enter or Escape continues; it also closes itself after a while.
 */
import * as sfx from "./sound.js";

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const AUTO_CLOSE_MS = 12000;

/**
 * @param {{ name: string|null, character?: string|null, isMe?: boolean }} opts  name null = nobody could be decided
 * @returns {Promise<void>} resolves when the splash is dismissed
 */
export function showBaronSplash({ name, character = null, isMe = false }) {
  const root = document.getElementById("baron-splash");
  if (!root) return Promise.resolve();
  return new Promise((resolve) => {
    const confetti = Array.from({ length: 36 }, (_, i) => {
      const left = (i * 97) % 100, delay = (i % 12) * 0.12, dur = 2.6 + (i % 5) * 0.5, hue = (i * 47) % 360;
      return `<i class="bs__bit" style="left:${left}%;animation-delay:${delay}s;animation-duration:${dur}s;background:hsl(${hue} 80% 60%)"></i>`;
    }).join("");
    root.innerHTML = `
      <div class="bs__rays" aria-hidden="true"></div>
      <div class="bs__confetti" aria-hidden="true">${confetti}</div>
      <div class="bs__card">
        <div class="bs__crown" aria-hidden="true">♛</div>
        ${name ? `<h2 class="bs__title"><span class="bs__name">${esc(name)}</span> je Baron${character ? ` za postavu <span class="bs__char">${esc(character)}</span>` : ""}!</h2>
                  ${isMe ? `<p class="bs__sub">Gratulujeme, vyhrál jsi!</p>` : ""}`
               : `<h2 class="bs__title">Dnes žádný Baron</h2><p class="bs__sub">Remíza se nepodařilo rozhodnout.</p>`}
        <button class="btn bs__btn" type="button">Pokračovat</button>
      </div>`;
    root.hidden = false;
    if (name) sfx.partyPopper();

    let done = false;
    const close = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      document.removeEventListener("keydown", onKey, true);
      root.hidden = true;
      root.innerHTML = "";
      resolve();
    };
    const onKey = (e) => { if (e.key === "Escape" || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); close(); } };
    const timer = setTimeout(close, AUTO_CLOSE_MS);
    root.onclick = close;
    document.addEventListener("keydown", onKey, true);
    root.querySelector(".bs__btn")?.focus({ preventScroll: true });
  });
}
