/**
 * client/js/vydrz/piles.js
 *
 * The card decks area shared by both Výdrž views: the three decks that can be
 * inspected (postava, lokace, existence) and the drink deck, which is one card
 * with the remaining count of every drink next to it (not inspectable).
 * Presentation only; every number comes from the server snapshot.
 */
import { cardBack, DRINKS } from "./cards.js";

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** @returns {string} markup with .vy-piles and .vy-drawn */
export function pilesHtml(state) {
  const d = state.decks, left = state.drinks_left ?? {};
  const piles = [["postava", "Postavy", d.postava], ["lokace", "Lokace", d.lokace], ["existence", "Existence", d.existence]]
    .map(([kind, label, n]) => `
      <button class="vy-pile" type="button" data-pile="${kind}" ${n ? "" : "disabled"} aria-label="Prohlédnout balíček: ${label}">
        <span class="vy-pile__stack">${cardBack(kind)}</span>
        <span class="vy-pile__label">${label} <b>${n}</b></span>
      </button>`).join("");
  const drinks = DRINKS.map(k =>
    `<li data-drink="${k.id}"><i class="vc-dot" style="background:${k.color}"></i><span>${esc(k.name)}</span><b>${left[k.id] ?? 0}</b></li>`).join("");
  return `<div class="vy-piles">${piles}</div>
    <div class="vy-drawn">
      <div class="vy-drawn__deck"><span class="vy-pile__stack">${cardBack("chlast")}</span>
        <span class="vy-pile__label">Chlast <b>${d.chlast}</b></span></div>
      <ul class="vy-drinks-left" aria-label="Zbývající chlast podle druhu">${drinks}</ul>
    </div>`;
}

/** Click on a deck opens the inspector with the cards that are left in it. */
export function onPileClick(container, getState, inspect) {
  container.addEventListener("click", (e) => {
    const pile = e.target.closest("[data-pile]");
    const cards = pile && getState()?.decks_cards?.[pile.dataset.pile];
    if (cards?.length) inspect(cards, cards[0].uid);
  });
}
