# Baron-card-game

Multiplayer online card game inspired by Bang!. See [ARCHITECTURE.md](ARCHITECTURE.md) for design rules.

## Hraní s kamarády (jedno spuštění)

Hru pouští jeden člověk (hostitel) na svém počítači. Ostatní hráči nic neinstalují, jen otevřou odkaz
v prohlížeči (telefon i počítač) a napíšou jméno. Hostem hry je ten, kdo založí místnost.

### Co potřebuješ

- **Python 3.10 nebo novější** ([python.org](https://www.python.org/downloads/)). Na Windows při instalaci
  zaškrtni **„Add python.exe to PATH“**.
- Připojení k internetu při prvním spuštění (stáhnou se závislosti a nástroj `cloudflared` pro veřejný odkaz).

### Spuštění

| Systém | Jak |
|---|---|
| **Windows** | dvojklik na `start.bat` (nebo v příkazové řádce ve složce hry: `start.bat`) |
| **Linux / macOS / WSL** | v terminálu ve složce hry: `./start.sh` |

Při prvním spuštění skript sám vytvoří složku `.venv` s Pythonem a nainstaluje závislosti (chvíli to trvá),
spustí server a vypíše **odkaz pro ostatní**. Odkaz funguje z libovolné sítě: skript otevře bezplatný
„quick tunnel“ přes `cloudflared` (stáhne se jednorázově do `.bin/`, účet není potřeba). Hostitel se připojí
na `http://localhost:8000`, kamarádi na vypsaný odkaz. Konec: **Ctrl+C** (nebo zavři okno).

### Možnosti

| Co | Windows | Linux / macOS |
|---|---|---|
| jen místní síť, nic se nestahuje (hráči jdou na `http://<IP>:8000`) | `start.bat --lan` | `./start.sh --lan` |
| jiný port (výchozí 8000) | `set PORT=9000` a pak `start.bat` | `PORT=9000 ./start.sh` |

### Když něco nejde

- **Windows: „Chybí Python 3.10+“** – nainstaluj Python s PATH (viz výše), zavři a znovu otevři okno a spusť znovu.
  Na Windows může Defender nebo firewall při prvním spuštění chtít povolení; povol ho pro soukromé sítě.
- **„Port 8000 je už obsazený“** – spusť s jiným portem (tabulka výše).
- **Veřejný odkaz se nevytvořil** – hostitel i hráči ve stejné síti můžou hrát přes `--lan`; jinak zkontroluj internet
  nebo nainstaluj `cloudflared` ručně ([návod](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)).
- Odkaz je veřejný, kdo ho má, může hrát. Po ukončení skriptu přestane fungovat.

Za běhu hry:

- Když se hráči na mobilu ztratí spojení (přepnutí aplikace, zhasnutá obrazovka), hra se
  připojí znovu sama. Do rozehrané hry se jde vrátit i novým oknem pod **stejným jménem**.
  Hra na odpojeného hráče čeká; host ho může v jeho kartě (klepnutí na jméno) odebrat
  tlačítkem „Hrát bez něj/ní“ a hraje se za něj automaticky.
- Na konci hry si hráči nejdřív prohlédnou poslední kolo. Host pak klepne na
  „Ohlásit vítěze“ (animace „je Baron!“ proběhne jednou) a nakonec může všechny vrátit
  do lobby tlačítkem „Zpět do lobby“ (jde změnit mód a hrát znovu bez nového kódu).

## Setup (vývoj)

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements-dev.txt

## Run the server

    PYTHONPATH=server:. uvicorn networking.server:app --port 8000

## Struktura repozitáře

Stručná mapa „kam sáhnout“ je v [STRUCTURE.md](STRUCTURE.md).

## Run the tests

    pytest

`pytest.ini` puts `server/` and the repo root on the import path.

Browser tests (`tests/e2e/`) drive two Chromium tabs against a live server.
They need Playwright's browser (`playwright install chromium`) and are
skipped automatically when Chromium is not available. Skip them explicitly
with `pytest --ignore=tests/e2e`.

The server also serves the browser client, so after starting it, open
`http://localhost:8000/`.

## Previewing the UI (phone or desktop)

The browser client has a **mock mode** that needs no game server. It uses fake
players and placeholder cards, only so the layout and touch interaction can be
judged. It contains no game rules, and a purple "Demo mode" ribbon marks it.

- Local, with the real server running: `http://localhost:8000/?mock=1`
  (`?mock=table` jumps straight to the table; add `&bots=6` for a 7-player layout).
- Single static file, any host or device:

      python tools/build_preview.py preview/index.html

  The output is one self-contained HTML file (needs Node for the bundler).

### Opening the dev server on your phone

Same Wi-Fi: start the server on all interfaces and open `http://<your-computer-ip>:8000/`.

    PYTHONPATH=server:. uvicorn networking.server:app --host 0.0.0.0 --port 8000

Elsewhere (HTTPS, any network): `cloudflared tunnel --url http://localhost:8000`
or `ngrok http 8000` prints a public URL. WebSockets work through both.

### Real vs mock

| | Real | Mock |
|---|---|---|
| Files | `client/js/core/socket.js` + the Python server | `client/js/mock/mock-socket.js` |
| Cards, turns, hands | server-authoritative | fake, no rules |
| Everything else in `client/` (store, renderer, controller, CSS) | shared by both | shared by both |

## Game modes

| Mode | State |
|---|---|
| **Výdrž** | Implemented (rules below). Placeholder card data and artwork. |
| **Řežba** | Not implemented. Shown disabled in the lobby. The older prototype engine in `server/game` is only a stand-in and is reachable through a dev hook. |

The host picks the mode in the lobby. Code lives in `server/modes/<mode>/`;
the server owns all state and rules, the client only renders snapshots.

### Výdrž

`server/modes/vydrz/` (pure Python, tested without networking):
each player gets one character, 8 locations (one of each number 1-8) and 8
existences, all public. Each of the 8 rounds every player secretly picks one
location + one existence; when all have picked, 3 drink cards are drawn and a
player's number is the sum of those drinks' stats on their character, location
and existence. The highest total wins the round and keeps the location (its
number is added to their score); ties are broken by drawing one drink at a
time and counting characters only. Highest final score wins, lowest loses.

Card content is data: `server/modes/vydrz/data/vydrz.json`. It is currently
random placeholder content from `python tools/generate_vydrz_data.py` (fixed
seed). Replace the JSON with the real cards; the loader validates it.

Settings (per device, in the lobby/table): sound on/off. Manual score counting
is planned; only automatic counting with animation exists today.
