"""Leaving a room / game, host-only room settings, and the one-file server setup."""
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[2]
MODES = {"action game": "rezba", "automatic": "vydrz", "manual": "vydrz_rucne"}


def test_start_script_is_valid_and_executable():
    script = ROOT / "start.sh"
    assert script.exists() and script.stat().st_mode & 0o111, "start.sh must be executable"
    subprocess.run(["bash", "-n", str(script)], check=True)                 # syntax check


def test_leave_room_from_the_lobby_returns_to_the_start_page(page_factory, base_url):
    page = page_factory()
    page.goto(f"{base_url}?mock=1&bots=2")
    expect(page.locator("#lobby-panel")).to_be_visible()
    page.click("#leave-btn")
    expect(page.locator("#join-panel")).to_be_visible(timeout=8000)        # the demo is over, not restarted
    expect(page.locator("#lobby-panel")).to_be_hidden()
    assert page.evaluate("sessionStorage.getItem('baron.demo')") is None


@pytest.mark.parametrize("label", list(MODES))
def test_game_cross_asks_in_the_page_and_leaves(page_factory, base_url, label):
    page = page_factory()
    page.goto(f"{base_url}?mock=table&mode={MODES[label]}&bots=2")
    cross = {"action game": "#leave-game-btn", "automatic": "#vy-leave-btn", "manual": "#vm-leave-btn"}[label]
    expect(page.locator(cross)).to_be_visible(timeout=10000)
    page.click(cross)
    dialog = page.locator("#confirm-dialog")
    expect(dialog).to_be_visible()                                        # an in-page dialog, not window.confirm
    expect(dialog).to_contain_text("Opustit hru?")
    page.click('#confirm-dialog [data-answer="no"].btn')                  # "Zůstat"
    expect(dialog).to_be_hidden()
    expect(page.locator(cross)).to_be_visible()                           # still in the game
    page.click(cross)
    page.click('#confirm-dialog [data-answer="yes"]')                     # "Opustit"
    expect(page.locator("#join-panel")).to_be_visible(timeout=8000)


def test_escape_closes_the_leave_dialog(page_factory, base_url):
    page = page_factory()
    page.goto(f"{base_url}?mock=table&bots=2")
    page.click("#vy-leave-btn")
    expect(page.locator("#confirm-dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#confirm-dialog")).to_be_hidden()


def test_real_server_leave_and_host_only_room_settings(browser, base_url):
    alice = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    bob = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    alice.goto(base_url)
    alice.fill("#display-name-input", "Alice")
    alice.click("#create-btn")
    expect(alice.locator("#lobby-panel")).to_be_visible()
    room = alice.locator("#room-id-display").inner_text().strip()
    bob.goto(base_url)
    bob.fill("#display-name-input", "Bob")
    bob.fill("#room-id-input", room)
    bob.click("#join-btn")
    expect(bob.locator("#lobby-panel")).to_be_visible()

    # Room settings (mode, counting variant) belong to the creator; the others only see them.
    expect(alice.locator("#mode-note")).to_be_hidden()
    expect(bob.locator("#mode-note")).to_be_visible()
    expect(bob.locator('#mode-switch [data-mode="vydrz"]')).to_be_disabled()
    expect(bob.locator("#variant-switch .seg__btn").first).to_be_disabled()
    expect(bob.locator("#start-game-btn")).to_be_hidden()
    # ... while the display settings stay on each device.
    bob.click("#lobby-settings-btn")
    expect(bob.locator("#vy-settings [data-set-speed]")).to_have_count(4)
    bob.click("#vy-settings button[data-close]")

    # The host leaves: the server removes Alice at once and Bob becomes the host.
    alice.click("#leave-btn")
    expect(alice.locator("#join-panel")).to_be_visible(timeout=8000)
    expect(bob.locator("#lobby-player-list li")).to_have_count(1, timeout=8000)
    expect(bob.locator("#lobby-player-list")).not_to_contain_text("Alice")
    expect(bob.locator("#mode-note")).to_be_hidden()                       # Bob is the host now
    expect(bob.locator('#mode-switch [data-mode="vydrz"]')).to_be_enabled()
