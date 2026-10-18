"""Two real browser tabs play through lobby -> start -> draw -> end turn."""
import re

from playwright.sync_api import expect


def _join(page, base_url, name, room_id=""):
    page.goto(base_url)
    page.fill("#display-name-input", name)
    if room_id:
        page.fill("#room-id-input", room_id)
        page.click("#join-btn")
    else:
        page.click("#create-btn")
    expect(page.locator("#lobby-panel")).to_be_visible()


def test_two_players_play_a_turn(browser, base_url):
    errors: list[str] = []
    alice = browser.new_context().new_page()
    bob = browser.new_context().new_page()
    for pg in (alice, bob):
        pg.on("pageerror", lambda e: errors.append(str(e)))

    _join(alice, base_url, "Alice")
    room_id = alice.locator("#room-id-display").inner_text().strip()
    assert room_id and room_id != "—"
    _join(bob, base_url, "Bob", room_id)

    # This test drives the older action-game prototype, which the UI no longer
    # offers (Řežba is disabled), so select it through the dev hook.
    alice.evaluate("window.__app.socket.send('set_mode', { mode: 'rezba' })")
    expect(bob.locator("#mode-switch .is-on")).to_contain_text("Řežba")

    # Only the host sees an enabled Start button.
    expect(alice.locator("#start-game-btn")).to_be_enabled()
    expect(bob.locator("#start-game-btn")).to_be_hidden()
    alice.click("#start-game-btn")

    for pg in (alice, bob):
        expect(pg.locator("#game-panel")).to_be_visible()
        expect(pg.locator("#hand-area .hand-card").first).to_be_visible()

    # Whoever's turn it is draws, then ends the turn.
    def active():
        for pg in (alice, bob):
            if pg.locator('[data-action="draw"]:visible').count():
                return pg
        return None

    for _ in range(50):
        mover = active()
        if mover:
            break
        alice.wait_for_timeout(100)
    assert mover is not None, "nobody can draw"
    other = bob if mover is alice else alice

    mover.click('[data-action="draw"]')
    expect(mover.locator("#my-hp-display")).to_have_text(re.compile(r"\d+/\d+"))
    expect(mover.locator('[data-action="end-turn"]')).to_be_visible()
    mover.click('[data-action="end-turn"]')

    # If the hand is over the limit, end-turn only moves to the discard phase:
    # discard by clicking cards, then end the turn again.
    end_btn = mover.locator('[data-action="end-turn"]')
    for _ in range(40):
        if other.locator('[data-action="draw"]:visible').count():
            break
        if end_btn.is_visible() and end_btn.is_enabled():
            hp = int(mover.locator("#my-hp-display").inner_text().split("/")[0])
            hand = mover.locator("#hand-area .hand-card")
            in_discard = mover.locator("#turn-indicator").get_attribute("data-phase") == "discard"
            if in_discard and hand.count() > hp:
                hand.first.click()
            else:
                end_btn.click()
        mover.wait_for_timeout(250)

    expect(other.locator('[data-action="draw"]')).to_be_visible()
    assert not errors, errors
