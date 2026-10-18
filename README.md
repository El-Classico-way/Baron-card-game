# Baron-card-game

Multiplayer online card game inspired by Bang!. See [ARCHITECTURE.md](ARCHITECTURE.md) for design rules.

## Setup

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements-dev.txt

## Run the server

    PYTHONPATH=server:. uvicorn networking.server:app --port 8000

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
