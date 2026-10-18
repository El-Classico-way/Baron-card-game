/**
 * client/js/mock/mock-vydrz.js
 *
 * MOCK of the Výdrž server engine, for the offline demo only. It mirrors the
 * rules implemented in server/modes/vydrz/game.py closely enough to exercise
 * the UI, using the same placeholder card data. The real server remains the
 * source of truth; nothing here is used when a server is connected.
 */

import DATA from "./vydrz-data.js";

const DRINKS = DATA.drinks.map(d => d.id);
const ROUNDS = 8;
const shuffle = (a) => { const r = [...a]; for (let i = r.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [r[i], r[j]] = [r[j], r[i]]; } return r; };
const leaders = (vals, highest = true) => {
  const best = (highest ? Math.max : Math.min)(...Object.values(vals));
  return Object.keys(vals).filter(k => vals[k] === best);
};


/** Like the server: the final ranking stays hidden until the host announces the winner. */
export function visibleResult(last, announced) {
  if (!last || !last.final || announced) return last;
  return { ...last, final: null, final_pending: true };
}

export class MockVydrz {
  /** @param {{id: string, name: string}[]} players  @param {(viewerId) => void} onChange */
  constructor(players, onChange) {
    this._onChange = onChange;
    this.phase = "choosing";
    this.announced = false;
    this.round = 1;
    this.last = null;
    this._timers = [];

    const chars = shuffle(DATA.characters);
    this.players = players.map((p, i) => ({
      player_id: p.id, display_name: p.name, score: 0, won: [], pick: null,
      character: { ...chars[i], uid: `c-${chars[i].id}`, kind: "postava" },
      locations: [], existences: [],
    }));
    for (let n = 1; n <= 8; n++) {
      const pile = shuffle(DATA.locations.filter(l => l.number === n));
      this.players.forEach((p, i) => p.locations.push({ ...pile[i], uid: `l-${pile[i].id}`, kind: "lokace" }));
    }
    const ex = shuffle(DATA.existences);
    this.players.forEach((p, i) => { p.existences = ex.slice(i * 8, i * 8 + 8).map(e => ({ ...e, uid: `e-${e.id}`, kind: "existence" })); });
    this.deck = shuffle(DATA.drinks.flatMap(d => Array.from({ length: DATA.drink_copies }, () => d.id)));
    this.discard = [];
    this._scheduleBots();
  }

  stop() { this._timers.forEach(clearTimeout); this._timers = []; }

  _scheduleBots() {
    this.players.filter(p => p.player_id.startsWith("bot")).forEach((bot, i) => {
      this._timers.push(setTimeout(() => {
        if (this.phase !== "choosing" || bot.pick) return;
        const l = bot.locations[Math.floor(Math.random() * bot.locations.length)];
        const e = bot.existences[Math.floor(Math.random() * bot.existences.length)];
        this.choose(bot.player_id, l.uid, e.uid);
      }, 1200 + i * 700 + Math.random() * 600));
    });
  }

  _draw() {
    if (!this.deck.length) { this.deck = shuffle(this.discard); this.discard = []; }
    const d = this.deck.pop(); this.discard.push(d); return d;
  }

  choose(pid, locUid, exUid) {
    if (this.phase !== "choosing") return "Teď se nevybírá.";
    const p = this.players.find(x => x.player_id === pid);
    if (!p?.locations.some(l => l.uid === locUid) || !p.existences.some(e => e.uid === exUid)) return "Tuhle kartu nemáš.";
    p.pick = [locUid, exUid];
    if (this.players.every(x => x.pick)) this._resolve();
    this._onChange();
    return "";
  }

  announce() {
    if (this.phase !== "finished") return "Hra ještě neskončila.";
    this.announced = true;
    this._onChange();
    return null;
  }

  nextRound() {
    if (this.phase !== "reveal") return "Kolo ještě není vyhodnocené.";
    this.round += 1; this.phase = "choosing";
    this.players.forEach(p => { p.pick = null; });
    this._scheduleBots();
    this._onChange();
    return "";
  }

  _breakTie(tied, highest) {
    const steps = [];
    while (tied.length > 1 && steps.length < 30) {
      const drink = this._draw();
      const values = Object.fromEntries(tied.map(pid => [pid, this.players.find(p => p.player_id === pid).character.stats[drink]]));
      tied = leaders(values, highest);
      steps.push({ drink, values, remaining: [...tied] });
    }
    return [tied, steps];
  }

  _resolve() {
    const picks = Object.fromEntries(this.players.map(p => [p.player_id, [
      p.locations.find(l => l.uid === p.pick[0]), p.existences.find(e => e.uid === p.pick[1])]]));
    const drawn = [this._draw(), this._draw(), this._draw()];
    const totals = Object.fromEntries(this.players.map(p => [p.player_id, 0]));
    const rows = drawn.map((drink) => {
      const per = {};
      for (const p of this.players) {
        const [l, e] = picks[p.player_id];
        const c = { postava: p.character.stats[drink], lokace: l.stats[drink] ?? null, existence: e.stats[drink] ?? null };
        c.total = c.postava + (c.lokace ?? 0) + (c.existence ?? 0);
        totals[p.player_id] += c.total; per[p.player_id] = c;
      }
      return { drink, players: per };
    });
    const [remaining, tiebreak] = this._breakTie(leaders(totals), true);
    const winner = remaining.length === 1 ? remaining[0] : null;
    const before = Object.fromEntries(this.players.map(p => [p.player_id, p.score]));
    let awarded = 0;
    if (winner) {
      const w = this.players.find(p => p.player_id === winner);
      const loc = picks[winner][0]; w.won.push(loc); w.score += loc.number; awarded = loc.number;
    }
    for (const p of this.players) {
      const [l, e] = picks[p.player_id];
      p.locations = p.locations.filter(x => x.uid !== l.uid);
      p.existences = p.existences.filter(x => x.uid !== e.uid);
    }
    this.last = {
      round: this.round, drawn: rows, totals,
      picks: Object.fromEntries(Object.entries(picks).map(([pid, [l, e]]) => [pid, { lokace: l, existence: e }])),
      tiebreak, winner_id: winner, awarded, scores_before: before,
      scores_after: Object.fromEntries(this.players.map(p => [p.player_id, p.score])), final: null,
    };
    if (this.round >= ROUNDS) { this.last.final = this._finish(); this.phase = "finished"; } else this.phase = "reveal";
  }

  _finish() {
    const scores = Object.fromEntries(this.players.map(p => [p.player_id, p.score]));
    const [w, firstSteps] = this._breakTie(leaders(scores, true), true);
    const winner = w.length === 1 ? w[0] : null;
    const lastGroup = leaders(scores, false).filter(id => id !== winner);
    const [l, lastSteps] = lastGroup.length ? this._breakTie(lastGroup, false) : [[], []];
    const loser = l.length === 1 ? l[0] : null;
    const order = Object.keys(scores).sort((a, b) =>
      (scores[b] - scores[a]) || ((b === winner) - (a === winner)) || ((a === loser) - (b === loser)));
    return { winner_id: winner, loser_id: loser, tiebreak_first: firstSteps, tiebreak_last: lastSteps,
      ranking: order.map((pid, i) => ({ player_id: pid, score: scores[pid], place: i + 1 })) };
  }

  snapshot(viewer) {
    return {
      mode: "vydrz", phase: this.phase, round: this.round, rounds_total: ROUNDS, you: viewer,
      players: this.players.map(p => ({
        player_id: p.player_id, display_name: p.display_name, score: p.score,
        character: p.character, locations: p.locations, existences: p.existences, won: p.won,
        locked: !!p.pick,
        pick: p.pick && (p.player_id === viewer || this.phase !== "choosing")
          ? { location_uid: p.pick[0], existence_uid: p.pick[1] } : null,
      })),
      decks: { chlast: this.deck.length, chlast_discard: this.discard.length,
        lokace: 64 - 8 * this.players.length, existence: 64 - 8 * this.players.length,
        postava: DATA.characters.length - this.players.length },
      last_result: visibleResult(this.last, this.announced), winner_announced: this.announced,
    };
  }
}
