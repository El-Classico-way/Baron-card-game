#!/usr/bin/env python3
"""
Generate PLACEHOLDER card data for the Výdrž mode.

Characters are fixed (names and zero-sum stats below). Location names come from
server/modes/vydrz/data/mista.txt, existence names from prezdivky.txt (both temporary
lists). Location/existence stats are random stand-ins with a fixed seed, so the output
is reproducible. Replace server/modes/vydrz/data/vydrz.json with the real content when
it exists; nothing else needs to change.

Writes two copies of the same data:
  server/modes/vydrz/data/vydrz.json    (read by the game server)
  client/js/mock/vydrz-data.js        (used only by the offline demo/mock)

    python tools/generate_vydrz_data.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DRINKS = [
    ("pivo", "Pivo"), ("vino", "Víno"), ("jahudka", "Jahůdka"), ("gin", "Gin"),
    ("rum", "Rum"), ("slivka", "Slivka"), ("vodka", "Vodka"), ("cider", "Cider"),
    ("tatran", "Tatran"), ("jager", "Jäger"),
]
DRINK_IDS = [d for d, _ in DRINKS]

LOCATIONS_PER_NUMBER = 8      # 8 numbers x 8 = 64; enough to deal 7 players one each
EXISTENCES = 64               # enough to deal 8 each to 7 players
DRINK_COUNT = {"mean": 20, "spread": 5}   # per game each drink kind gets mean +/- spread cards

# id, name, flag, stats in the order of DRINKS (pivo vino jahudka gin rum slivka vodka cider tatran jager).
# Every character sums to 0.
CHARACTERS = [
    ("stepan",      "Štěpán",       "cz", [ 2, -1,  0,  1, -2,  3, -1,  0,  1, -3]),
    ("fanda",       "Fanda",        "cz", [ 3, -2,  1, -1,  2, -3,  0,  1, -2,  1]),
    ("tomas",       "Tomáš",        "cz", [-1,  3,  2, -2,  0,  1, -3,  2, -1, -1]),
    ("salek",       "Šálek",        "cz", [ 1,  0, -3,  3, -1,  2,  1, -2,  2, -3]),
    ("jurda",       "Jurda",        "cz", [-2,  1,  3,  0,  2, -1, -2,  3, -3, -1]),
    ("wirtak",      "Wirťák",       "cz", [ 3, -3,  0,  2, -1, -2,  3, -1,  1, -2]),
    ("porucik-dan", "Poručík Dan",  "cz", [-3,  2, -1,  1,  3, -2,  2, -1,  0, -1]),
    ("frodo",       "Frodo",        "cz", [ 0,  2,  3, -3,  1,  2, -2,  1, -3, -1]),
    ("dj-quest",    "Dj. Quest",    "cz", [ 1, -1, -2,  2, -3,  0,  3,  3, -2, -1]),
    ("miki",        "Miki",         "cz", [-1,  1,  2, -3,  1,  3, -1, -2,  2, -2]),
    ("martin",      "Martin",       "sk", [ 2,  0, -1, -1,  3, -3,  1,  2, -1, -2]),
]

DATA_DIR = ROOT / "server" / "modes" / "vydrz" / "data"


def read_names(filename: str, needed: int) -> list[str]:
    lines = (DATA_DIR / filename).read_text(encoding="utf-8").splitlines()
    names = [l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#")]
    if len(set(names)) != len(names):
        raise SystemExit(f"{filename}: duplicate names")
    if len(names) < needed:
        raise SystemExit(f"{filename}: need at least {needed} names, found {len(names)}")
    return names[:needed]


def stat(rng: random.Random, allow_zero: bool) -> int:
    values = [-3, -2, -1, 0, 1, 2, 3] if allow_zero else [-3, -2, -1, 1, 2, 3]
    return rng.choice(values)


def partial_stats(rng: random.Random) -> dict[str, int]:
    """2 or 3 drinks with a non-zero value (locations and existences)."""
    chosen = rng.sample(DRINK_IDS, rng.choice([2, 3]))
    return {d: stat(rng, allow_zero=False) for d in sorted(chosen, key=DRINK_IDS.index)}


def build(seed: int = 2024) -> dict:
    rng = random.Random(seed)
    places = read_names("mista.txt", 8 * LOCATIONS_PER_NUMBER)
    nicknames = read_names("prezdivky.txt", EXISTENCES)
    for cid, name, _flag, values in CHARACTERS:
        assert len(values) == len(DRINK_IDS) and sum(values) == 0, f"{name}: stats must sum to 0"
    return {
        "pack_id": "vydrz-placeholder",
        "version": "0.2.0",
        "note": "Characters are final; location/existence names are temporary lists, stats are random stand-ins.",
        "drink_count": DRINK_COUNT,
        "drinks": [{"id": d, "name": n} for d, n in DRINKS],
        "characters": [
            {"id": cid, "name": name, "flag": flag, "stats": dict(zip(DRINK_IDS, values))}
            for cid, name, flag, values in CHARACTERS
        ],
        "locations": [
            {
                "id": f"loc-{n}-{k}",
                "number": n,
                "name": places[(n - 1) * LOCATIONS_PER_NUMBER + k - 1],
                "description": "Zástupný popis lokace. Skutečný text přijde později.",
                "stats": partial_stats(rng),
            }
            for n in range(1, 9)
            for k in range(1, LOCATIONS_PER_NUMBER + 1)
        ],
        "existences": [
            {
                "id": f"ex-{k:02d}",
                "name": nicknames[k - 1],
                "description": "Zástupný popis existence. Skutečný text přijde později.",
                "stats": partial_stats(rng),
            }
            for k in range(1, EXISTENCES + 1)
        ],
    }


def main() -> None:
    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1)
    (ROOT / "server" / "modes" / "vydrz" / "data" / "vydrz.json").write_text(text + "\n", encoding="utf-8")
    js = (
        "// GENERATED by tools/generate_vydrz_data.py. Placeholder data for the mock only.\n"
        f"export default {json.dumps(data, ensure_ascii=False)};\n"
    )
    (ROOT / "client" / "js" / "mock" / "vydrz-data.js").write_text(js, encoding="utf-8")
    print("wrote vydrz.json and vydrz-data.js")


if __name__ == "__main__":
    main()
