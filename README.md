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
