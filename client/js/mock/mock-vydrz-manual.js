/**
 * client/js/mock/mock-vydrz-manual.js
 *
 * MOCK of the Výdrž (ruční) server engine for the offline demo only. It
 * mirrors server/modes/vydrz_manual/game.py; the real server stays the source
 * of truth. Bots plan randomly and then answer: mostly right, sometimes a bit
 * low (Blbec) or too high (Švindlíř), so that every tag can be seen.
 */

import DATA from "./vydrz-data.js";

const ROUNDS = 8;
const CHEATER_VALUE = -50;      // what a "svindlir" entry counts as
const shuffle = (a) => { const r = [...a]; for (let i = r.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [r[i], r[j]] = [r[j], r[i]]; } return r; };
const leaders = (vals, highest = true) => {
  const best = (highest ? Math.max : Math.min)(...Object.values(vals));
  return Object.keys(vals).filter(k => vals[k] === best);
};

import { visibleResult } from "./mock-vydrz.js";

export class MockManual {
  constructor(players, onChange) {
    this._onChange = onChange;
    this.announced = false;
    this.phase = "planning"; this.round = 0; this.last = null; this.calc = null;
    this._steps = []; this._ctx = null; this._final = null; this._drawId = 0; this._timers = [];

    const chars = shuffle(DATA.characters);
    this.players = players.map((p, i) => ({
      player_id: p.id, display_name: p.name, score: 0, won: [], plan: null,
      character: { ...chars[i], uid: `c-${chars[i].id}`, kind: "postava" }, locations: [], existences: [],
    }));
    for (let n = 1; n <= 8; n++) {
      const pile = shuffle(DATA.locations.filter(l => l.number === n));
      this.players.forEach((p, i) => p.locations.push({ ...pile[i], uid: `l-${pile[i].id}`, kind: "lokace" }));
    }
    const ex = shuffle(DATA.existences);
    this.players.forEach((p, i) => { p.existences = ex.slice(i * 8, i * 8 + 8).map(e => ({ ...e, uid: `e-${e.id}`, kind: "existence" })); });
    const dealt = new Set(this.players.flatMap(p => [p.character.uid, ...p.locations.map(l => l.uid), ...p.existences.map(e => e.uid)]));
    const rest = (list, kind, prefix) => list.map(c => ({ ...c, uid: `${prefix}-${c.id}`, kind })).filter(c => !dealt.has(c.uid));
    this._left = { postava: rest(DATA.characters, "postava", "c"), lokace: rest(DATA.locations, "lokace", "l"), existence: rest(DATA.existences, "existence", "e") };
    this.deck = shuffle(DATA.drinks.flatMap(d => Array.from({ length: DATA.drink_count.mean + Math.floor(Math.random() * (2 * DATA.drink_count.spread + 1)) - DATA.drink_count.spread }, () => d.id)));
    this.discard = [];

    this.players.filter(p => p.player_id.startsWith("bot")).forEach((bot, i) => {
      this._timers.push(setTimeout(() => {
        const l = shuffle(bot.locations), e = shuffle(bot.existences);
        this.setPlan(bot.player_id, l.map((x, k) => [x.uid, e[k].uid]));
      }, 1300 + i * 600));
    });
  }

  stop() { this._timers.forEach(clearTimeout); this._timers = []; }

  _draw() {
    if (!this.deck.length) { this.deck = shuffle(this.discard); this.discard = []; }
    const d = this.deck.pop(); this.discard.push(d); return d;
  }
  _p(pid) { return this.players.find(p => p.player_id === pid); }
  _pair(p, r) { return [p.locations.find(l => l.uid === p.plan[r - 1][0]), p.existences.find(e => e.uid === p.plan[r - 1][1])]; }

  setPlan(pid, pairs) {
    const p = this._p(pid);
    if (this.phase !== "planning" || !p) return "Teď se neplánuje.";
    const ls = pairs.map(x => x[0]).sort(), es = pairs.map(x => x[1]).sort();
    if (pairs.length !== ROUNDS || ls.join() !== p.locations.map(l => l.uid).sort().join() || es.join() !== p.existences.map(e => e.uid).sort().join())
      return "Každou svou lokaci a existenci musíš použít přesně jednou.";
    p.plan = pairs;
    if (this.players.every(x => x.plan)) { this.round = 1; this._beginRound(); }
    this._onChange();
    return "";
  }

  submit(pid, value) {
    const c = this.calc;
    if (this.phase !== "calculating" || !c) return "Teď se nepočítá.";
    if (!c.participants.includes(pid)) return "V tomto počítání nejsi.";
    if (c.entries[pid]) return "Už jsi číslo zadal(a).";
    const right = c.correct[pid];
    c.entries[pid] = value === right ? { entered: value, value, tag: null }
      : value < right ? { entered: value, value, tag: "blbec" } : { entered: value, value: CHEATER_VALUE, tag: "svindlir" };
    if (Object.keys(c.entries).length === c.participants.length) this._complete();
    this._onChange();
    return "";
  }

  nextRound() {
    if (this.phase !== "reveal") return "Kolo ještě není vyhodnocené.";
    this.round += 1; this._beginRound(); this._onChange(); return "";
  }

  _beginRound() {
    this.phase = "calculating";
    const drinks = [this._draw(), this._draw(), this._draw()];
    const correct = {};
    for (const p of this.players) {
      const [l, e] = this._pair(p, this.round);
      correct[p.player_id] = drinks.reduce((s, d) => s + p.character.stats[d] + (l.stats[d] ?? 0) + (e.stats[d] ?? 0), 0);
    }
    this._steps = [];
    this._ctx = { drawn: drinks, before: Object.fromEntries(this.players.map(p => [p.player_id, p.score])) };
    this.calc = { purpose: "round", drinks, participants: this.players.map(p => p.player_id), correct, tiebreak: false, draw_id: ++this._drawId, entries: {} };
    this._scheduleBots();
  }

  _tiebreak(purpose, tied) {
    const d = this._draw();
    this.calc = { purpose, drinks: [d], participants: [...tied], correct: Object.fromEntries(tied.map(pid => [pid, this._p(pid).character.stats[d]])),
      tiebreak: true, draw_id: ++this._drawId, entries: {} };
    this._scheduleBots();
  }

  _scheduleBots() {
    const c = this.calc;
    c.participants.filter(pid => pid.startsWith("bot")).forEach((pid, i) => {
      this._timers.push(setTimeout(() => {
        if (this.calc !== c || c.entries[pid]) return;
        const r = Math.random(), right = c.correct[pid];
        this.submit(pid, r < 0.78 ? right : r < 0.9 ? right - 1 - Math.floor(Math.random() * 2) : right + 1 + Math.floor(Math.random() * 3));
      }, 5200 + i * 900 + Math.random() * 2500));          // after the drink cards have been turned over
    });
  }

  _table(c) {
    return Object.fromEntries(Object.entries(c.entries).map(([pid, e]) => [pid, { value: e.value, tag: e.tag, entered: e.entered, correct: c.correct[pid] }]));
  }

  _complete() {
    const c = this.calc;
    this._steps.push({ drinks: [...c.drinks], tiebreak: c.tiebreak, purpose: c.purpose, table: this._table(c) });
    const left = leaders(Object.fromEntries(Object.entries(c.entries).map(([k, e]) => [k, e.value])), c.purpose !== "final_last");
    const ties = this._steps.filter(s => s.tiebreak && s.purpose === c.purpose).length;
    if (left.length > 1 && ties < 30) { this._tiebreak(c.purpose, left); return; }
    const decided = left.length === 1 ? left[0] : null;
    this.calc = null;
    if (c.purpose === "round") this._finishRound(decided);
    else {
      this._final[c.purpose === "final_first" ? "winner" : "loser"] = decided;
      this._final["steps_" + c.purpose] = this._steps.filter(s => s.purpose === c.purpose);
      this._advanceFinal();
    }
  }

  _finishRound(winner) {
    const picks = Object.fromEntries(this.players.map(p => [p.player_id, this._pair(p, this.round)]));
    let awarded = 0;
    if (winner) { const w = this._p(winner); const loc = picks[winner][0]; w.won.push(loc); w.score += loc.number; awarded = loc.number; }
    this.last = {
      round: this.round, drawn: this._ctx.drawn,
      picks: Object.fromEntries(Object.entries(picks).map(([pid, [l, e]]) => [pid, { lokace: l, existence: e }])),
      table: this._steps[0].table, tiebreak: this._steps.slice(1), winner_id: winner, awarded,
      scores_before: this._ctx.before, scores_after: Object.fromEntries(this.players.map(p => [p.player_id, p.score])), final: null,
    };
    for (const p of this.players) {
      const [l, e] = picks[p.player_id];
      p.locations = p.locations.filter(x => x.uid !== l.uid); p.existences = p.existences.filter(x => x.uid !== e.uid);
    }
    this._steps = [];
    if (this.round >= ROUNDS) { this._final = {}; this._advanceFinal(); } else this.phase = "reveal";
  }

  _advanceFinal() {
    const f = this._final, scores = Object.fromEntries(this.players.map(p => [p.player_id, p.score]));
    if (!("winner" in f)) {
      const g = leaders(scores, true);
      if (g.length === 1) f.winner = g[0]; else { this._steps = []; this.phase = "calculating"; this._tiebreak("final_first", g); return; }
    }
    if (!("loser" in f)) {
      const g = leaders(scores, false).filter(id => id !== f.winner);
      if (g.length === 1) f.loser = g[0]; else if (!g.length) f.loser = null; else { this._steps = []; this.phase = "calculating"; this._tiebreak("final_last", g); return; }
    }
    const order = Object.keys(scores).sort((a, b) => (scores[b] - scores[a]) || ((b === f.winner) - (a === f.winner)) || ((a === f.loser) - (b === f.loser)));
    this.last.final = { winner_id: f.winner, loser_id: f.loser, tiebreak_first: f.steps_final_first ?? [], tiebreak_last: f.steps_final_last ?? [],
      ranking: order.map((pid, i) => ({ player_id: pid, score: scores[pid], place: i + 1 })) };
    this.phase = "finished";
  }

  announce() {
    if (this.phase !== "finished") return "Hra ještě neskončila.";
    this.announced = true;
    this._onChange();
    return null;
  }

  snapshot(viewer) {
    const c = this.calc;
    let calc = null;
    if (c) {
      const me = this._p(viewer);
      const mine = c.purpose === "round" && c.participants.includes(viewer) ? (([l, e]) => ({ location: l, existence: e }))(this._pair(me, this.round)) : null;
      calc = { purpose: c.purpose, tiebreak: c.tiebreak, drinks: c.drinks, draw_id: c.draw_id, participants: c.participants,
        entries: Object.fromEntries(Object.entries(c.entries).map(([k, e]) => [k, { value: e.value, tag: e.tag }])), entered: !!c.entries[viewer], mine };
    }
    return {
      mode: "vydrz_rucne", phase: this.phase, round: this.round, rounds_total: ROUNDS, you: viewer,
      players: this.players.map(p => ({
        player_id: p.player_id, display_name: p.display_name, score: p.score, character: p.character,
        locations: p.locations, existences: p.existences, won: p.won, planned: !!p.plan,
        plan: p.player_id === viewer && p.plan ? p.plan.map(([l, e]) => ({ location_uid: l, existence_uid: e })) : null,
      })),
      decks_cards: this._left,
      drinks_left: Object.fromEntries(DATA.drinks.map(d => [d.id, this.deck.filter(x => x === d.id).length])),
      decks: { chlast: this.deck.length, chlast_discard: this.discard.length, lokace: 64 - 8 * this.players.length,
        existence: 64 - 8 * this.players.length, postava: DATA.characters.length - this.players.length },
      calc, last_result: visibleResult(this.last, this.announced), winner_announced: !!this.announced,
    };
  }
}
