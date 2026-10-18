/**
 * Client-side display settings (per device). Stored in localStorage, which
 * may be unavailable (private mode), so every access is guarded.
 *
 *   sound     bool           sound effects on/off
 *   scoring   "auto"|"manual"  "manual" counting is planned; only "auto" works today
 */
const KEY = "baron.settings";
const DEFAULTS = { sound: true, scoring: "auto" };

let cache = null;

export function getSettings() {
  if (cache) return cache;
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(KEY) ?? "{}") || {}; } catch { /* ignore */ }
  cache = { ...DEFAULTS, ...stored };
  return cache;
}

export function updateSettings(patch) {
  cache = { ...getSettings(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(cache)); } catch { /* ignore */ }
  return cache;
}
