/**
 * Tiny synthesised sound effects (Web Audio). No audio files needed.
 * The AudioContext is created on first use, which always happens inside a
 * user gesture (a tap), as browsers require.
 */
import { getSettings } from "./settings.js";

let ctx = null;

function audio() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try { ctx = new AC(); } catch { return null; }
  }
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

/** Warm up the context from a tap so later (timer-driven) sounds can play. */
export function unlockAudio() { audio(); }

function tone({ freq, dur = 0.14, type = "triangle", gain = 0.18, slideTo = null, delay = 0 }) {
  if (!getSettings().sound) return;
  const a = audio();
  if (!a) return;
  const t0 = a.currentTime + delay;
  const osc = a.createOscillator();
  const g = a.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  if (slideTo) osc.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(gain, t0 + 0.012);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(g).connect(a.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

/** A stat was added. Higher running total (or bigger value) pitches higher. */
export function tick(value, running) {
  const base = 330 + Math.max(-6, Math.min(12, running)) * 22;
  if (value > 0) tone({ freq: base + value * 40, slideTo: base + value * 40 + 90, dur: 0.16 });
  else tone({ freq: 190 + value * 12, slideTo: 120, type: "sawtooth", dur: 0.2, gain: 0.12 });
}

/** A drink card was turned over. */
export function flip() { tone({ freq: 520, slideTo: 260, type: "square", dur: 0.09, gain: 0.07 }); }

/** Round winner / big moment: a quick major arpeggio. */
export function fanfare() {
  [523, 659, 784, 1047].forEach((f, i) => tone({ freq: f, dur: 0.22, delay: i * 0.09, gain: 0.16 }));
}

/** Tie! A short ominous drop. */
export function tieSting() { tone({ freq: 300, slideTo: 150, type: "square", dur: 0.35, gain: 0.1 }); }

/** UI feedback for taps on cards. */
export function pop() { tone({ freq: 700, slideTo: 880, dur: 0.06, gain: 0.07 }); }
