"""Fixtures for browser tests: a live uvicorn server and a Chromium browser."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'server'}{os.pathsep}{ROOT}"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "networking.server:app",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{url}/lobby/rooms", timeout=0.5)
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def browser():
    with playwright_sync.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:  # browser binary not installed
            pytest.skip(f"Chromium unavailable: {exc}")
        # The opening deal takes ~10 s; most tests don't care, so contexts start with it
        # switched off (a test can turn it on by setting its own `baron.settings`).
        original = b.new_context

        def new_context(*args, **kwargs):
            ctx = original(*args, **kwargs)
            ctx.add_init_script(
                "try { if (!localStorage.getItem('baron.settings')) "
                "localStorage.setItem('baron.settings', JSON.stringify({deal: false})); } catch (e) {}")
            return ctx

        b.new_context = new_context
        yield b
        b.close()
