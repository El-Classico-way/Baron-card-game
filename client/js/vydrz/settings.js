/**
 * Client-side display settings (per device). Stored in localStorage, which
 * may be unavailable (private mode), so every access is guarded.
 *
 *   sound     bool           sound effects on/off
 *   deal      bool           animate the opening deal (pair by pair)
 *   speed     1..4           speed of the automatic counting animation:
 *                            1 = fastest (the original pace), 4 = slowest
 */
const KEY = "baron.settings";
const DEFAULTS = { sound: true, speed: 1, deal: true };

/** Labels for speeds 1..4 and the factor applied to every pause of the animation. */
export const SPEED_LABELS = ["Nejrychlejší", "Rychlá", "Pomalá", "Nejpomalejší"];
export const SPEED_FACTOR = [1, 1.7, 2.6, 4];

let cache = null;

export function getSettings() {
  if (cache) return cache;
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(KEY) ?? "{}") || {}; } catch { /* ignore */ }
  cache = { ...DEFAULTS, ...stored };
  if (![1, 2, 3, 4].includes(cache.speed)) cache.speed = 1;
  return cache;
}

export function updateSettings(patch) {
  cache = { ...getSettings(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(cache)); } catch { /* ignore */ }
  return cache;
}
