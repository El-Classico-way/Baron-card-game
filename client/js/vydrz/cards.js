/**
 * client/js/vydrz/cards.js
 *
 * HTML builders for the Výdrž card kinds. Pure functions, no state.
 *
 *   #postava    character: name on top, photo, stats for all 10 drinks
 *   #chlast     drink card
 *   #lokace     location: number, name, stats for 2-3 drinks
 *   #existence  like a location, without a number
 *
 * Artwork is placeholder only (logo disc, photo silhouette). What matters at
 * this stage is the card backs, the stat layout and that everything is
 * readable. Sizing is driven by one CSS variable (--vc-w) on the card, so the
 * same markup is used at hand size, mini size and in the inspector.
 */

export const DRINKS = [
  { id: "pivo",    name: "Pivo",    color: "#e3ac2f" },
  { id: "vino",    name: "Víno",    color: "#a63559" },
  { id: "jahudka", name: "Jahůdka", color: "#e0525f" },
  { id: "gin",     name: "Gin",     color: "#4fb0a6" },
  { id: "rum",     name: "Rum",     color: "#a8642f" },
  { id: "slivka",  name: "Slivka",  color: "#7a4cb8" },
  { id: "vodka",   name: "Vodka",   color: "#9fb3c8" },
  { id: "cider",   name: "Cider",   color: "#82c04a" },
  { id: "tatran",  name: "Tatran",  color: "#d27a35" },
  { id: "jager",   name: "Jäger",   color: "#3f8a4d" },
];
/** Short labels for the dense 10-stat grid on character cards. */
const SHORT = { pivo: "Pivo", vino: "Víno", jahudka: "Jahů", gin: "Gin", rum: "Rum", slivka: "Sliv", vodka: "Vodk", cider: "Cid", tatran: "Tatr", jager: "Jäg" };
const DRINK_BY_ID = Object.fromEntries(DRINKS.map(d => [d.id, d]));
export const drinkInfo = (id) => DRINK_BY_ID[id] ?? { id, name: id, color: "#888" };

/** "+2", "−1", "0" (typographic minus so it lines up with the plus). */
export function fmtStat(n) {
  if (n == null) return "—";
  return n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : "0";
}
const signClass = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "zero");

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const attr = (s) => esc(s).replace(/"/g, "&quot;");

export const KIND_TAG = { postava: "#postava", chlast: "#chlast", lokace: "#lokace", existence: "#existence" };

function statChip(drink, value, { dot = true, short = false } = {}) {
  const d = drinkInfo(drink);
  return `<span class="vc-stat vc-stat--${signClass(value)}" data-drink="${attr(drink)}" style="--drink:${d.color}">
    ${dot ? `<i class="vc-dot" style="background:${d.color}"></i>` : ""}<span class="vc-stat__name">${esc(short ? (SHORT[drink] ?? d.name) : d.name)}</span><b class="vc-stat__val">${fmtStat(value)}</b>
  </span>`;
}

/** Wrapper shared by all faces. extra = additional classes, attrs = raw attribute string. */
function face(kind, card, inner, { extra = "", attrs = "" } = {}) {
  return `<div class="vc vc--${kind} ${extra}" data-kind="${kind}" data-uid="${attr(card.uid ?? "")}" ${attrs}>
    ${inner}
    <div class="vc__tag">${KIND_TAG[kind]}</div>
  </div>`;
}

const FLAGS = {
  cz: `<rect width="30" height="15" fill="#fff"/><rect y="15" width="30" height="15" fill="#d7141a"/><path d="M0 0L15 15L0 30Z" fill="#11457e"/>`,
  sk: `<rect width="30" height="10" fill="#fff"/><rect y="10" width="30" height="10" fill="#0b4ea2"/><rect y="20" width="30" height="10" fill="#ee1c25"/>
       <path d="M7 6h10v9q0 5-5 7q-5-2-5-7z" fill="#ee1c25" stroke="#fff" stroke-width="1"/><path d="M12 8v10M9 11h6" stroke="#fff" stroke-width="1.6"/>
       <path d="M8.5 18q3.5-4 7 0q-1 2.5-3.5 3.5q-2.5-1-3.5-3.5z" fill="#0b4ea2"/>`,
};

/** Round country flag in the corner of a character card. */
function flagBadge(flag) {
  return FLAGS[flag] ? `<span class="vc__flag" data-flag="${attr(flag)}" aria-hidden="true"><svg viewBox="0 0 30 30" preserveAspectRatio="xMidYMid slice">${FLAGS[flag]}</svg></span>` : "";
}

/** Shrink long names (e.g. "Poručík Dan") so the title stays on one line next to the flag. */
const titleFit = (name) => Math.min(1, 9 / Math.max(String(name).length, 1) / 0.95).toFixed(2);

export function characterFront(card, opts = {}) {
  const stats = Object.entries(card.stats).map(([d, v]) => statChip(d, v, { short: !/vc--big/.test(opts.extra ?? "") })).join("");
  return face("postava", card, `
    <div class="vc__titlebar"><div class="vc__title" style="--fit:${titleFit(card.name)}">${esc(card.name)}</div>${flagBadge(card.flag)}</div>
    <div class="vc__photo" aria-hidden="true"><span class="vc__photo-mark">${esc(initials(card.name))}</span></div>
    <div class="vc__stats vc__stats--10">${stats}</div>`, opts);
}

export function locationFront(card, opts = {}) {
  const stats = Object.entries(card.stats).map(([d, v]) => statChip(d, v)).join("");
  return face("lokace", card, `
    <div class="vc__head"><span class="vc__number">${card.number}</span><span class="vc__title">${esc(card.name)}</span></div>
    <div class="vc__art" aria-hidden="true"></div>
    <div class="vc__stats">${stats}</div>`, opts);
}

export function existenceFront(card, opts = {}) {
  const stats = Object.entries(card.stats).map(([d, v]) => statChip(d, v)).join("");
  return face("existence", card, `
    <div class="vc__head"><span class="vc__title">${esc(card.name)}</span></div>
    <div class="vc__art" aria-hidden="true"></div>
    <div class="vc__stats">${stats}</div>`, opts);
}

export function drinkFront(drink, opts = {}) {
  const d = drinkInfo(drink);
  return face("chlast", { uid: "" }, `
    <div class="vc__drink" style="--drink:${d.color}"><i class="vc-dot vc-dot--big" style="background:${d.color}"></i>
      <span class="vc__drink-name">${esc(d.name)}</span></div>`, { ...opts, attrs: `data-drink="${attr(drink)}"` });
}

/** Card back for piles. Logo is a placeholder disc; the word "Baron" is coloured per kind. */
export function cardBack(kind, opts = {}) {
  return `<div class="vc vc--back vc--back-${kind} ${opts.extra ?? ""}" data-kind="${kind}" ${opts.attrs ?? ""}>
    <div class="vc__logo" aria-hidden="true">◈</div>
    <div class="vc__baron">Baron</div>
    <div class="vc__tag">${KIND_TAG[kind]}</div>
  </div>`;
}

/** Generic dispatcher on card.kind (server cards carry a "kind" field). */
export function cardFront(card, opts = {}) {
  switch (card.kind) {
    case "postava": return characterFront(card, opts);
    case "lokace": return locationFront(card, opts);
    case "existence": return existenceFront(card, opts);
    case "chlast": return drinkFront(card.drink, opts);
    default: return "";
  }
}

export function cardMeta(card) {
  if (card.kind === "lokace") return `Lokace ${card.number}`;
  if (card.kind === "existence") return "Existence";
  if (card.kind === "postava") return "Postava";
  return "Chlast";
}

function initials(name) {
  const parts = String(name).trim().split(/\s+/);
  return (parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "");
}
