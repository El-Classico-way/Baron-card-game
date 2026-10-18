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
