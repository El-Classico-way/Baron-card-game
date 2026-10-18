"""Reconnecting, returning to a seen result, announcing the winner, back to the lobby, desktop scrolling."""
import pytest
from playwright.sync_api import expect

COUNT_SOUNDS = """
window.__osc = 0;
const AC = window.AudioContext || window.webkitAudioContext;
if (AC) { const orig = AC.prototype.createOscillator; AC.prototype.createOscillator = function () { window.__osc++; return orig.call(this); }; }
"""


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.add_init_script(COUNT_SOUNDS)
    pg = ctx.new_page()
    pg.js_errors = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    yield pg
    ctx.close()


def _table(page, base_url, bots=2):
    page.goto(f"{base_url}?mock=table&bots={bots}")
    expect(page.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    expect(page.locator("#vy-locations .vy-slot")).to_have_count(8)


def _round(page, rnd):
    expect(page.locator("#vy-round")).to_have_text(f"Kolo {rnd} / 8", timeout=15000)
    page.locator("#vy-locations .vy-slot").first.click()
    page.locator("#vy-existences .vy-slot").first.click()
    page.click('[data-act="confirm"]')
    expect(page.locator(".vr__skip")).to_be_visible(timeout=15000)
    page.click(".vr__skip")


# ---------------------------------------------------------------------------

def test_reopening_a_result_shows_the_end_state_silently(page, base_url):
    _table(page, base_url)
    page.locator("#vy-locations .vy-slot").first.click()
    page.locator("#vy-existences .vy-slot").first.click()
    page.click('[data-act="confirm"]')
    expect(page.locator(".vr__next, .vr__waiting")).to_be_visible(timeout=40000)      # let it count to the end
    page.click(".vr__close")
    expect(page.locator(".vr__panel")).to_have_count(0)
    page.evaluate("window.__osc = 0")
    page.click('[data-act="result"]')
    # Everything is there at once: no ongoing counting, no sounds, no skip button.
    expect(page.locator(".vr__outcome")).to_be_visible()
    expect(page.locator(".vr__next")).to_be_visible()
    expect(page.locator(".vr__skip")).to_be_hidden()
    expect(page.locator("#vy-result.is-static")).to_have_count(1)
    page.wait_for_timeout(600)
    assert page.evaluate("window.__osc") == 0
    assert not page.js_errors


def test_host_announces_then_everyone_sees_the_baron_once_and_can_return_to_the_lobby(page, base_url):
    _table(page, base_url)
    for rnd in range(1, 9):
        _round(page, rnd)
        if rnd < 8:
            page.click(".vr__next")
    # Last round first, no winner yet.
    expect(page.locator(".vr__announce")).to_be_visible()
    expect(page.locator("#baron-splash")).to_be_hidden()
    expect(page.locator(".vr__final")).to_be_hidden()
    page.click(".vr__announce")
    expect(page.locator("#baron-splash .bs__title")).to_contain_text("je Baron!", timeout=8000)
    page.keyboard.press("Enter")
    expect(page.locator(".vr__ranking li")).to_have_count(3)

    # Leave the results and come back: the final table is there, the splash does not repeat, nothing sounds.
    page.click(".vr__close")
    page.evaluate("window.__osc = 0")
    page.click('[data-act="result"]')
    expect(page.locator(".vr__ranking li")).to_have_count(3)
    expect(page.locator("#baron-splash")).to_be_hidden()
    page.wait_for_timeout(500)
    assert page.evaluate("window.__osc") == 0

    # Back to the pre-game lobby, where the mode can be changed and a new game started.
    page.click(".vr__lobby")
    expect(page.locator("#lobby-panel")).to_be_visible(timeout=5000)
    expect(page.locator("#vydrz-panel")).to_be_hidden()
    page.click('#variant-switch [data-mode="vydrz_rucne"]')
    page.click("#start-game-btn")
    expect(page.locator("#vm-panel")).to_be_visible(timeout=8000)
    assert not page.js_errors


def test_desktop_wheel_scrolls_anywhere_on_the_screen(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1280, "height": 500})
    page = ctx.new_page()
    _table(page, base_url)
    top = lambda: page.evaluate("document.querySelector('.vy-scroll').scrollTop")
    assert page.evaluate("(() => { const s = document.querySelector('.vy-scroll'); return s.scrollHeight > s.clientHeight; })()")
    for x in (30, 640, 1250):                          # left edge, middle, right edge
        page.evaluate("document.querySelector('.vy-scroll').scrollTop = 0")
        page.mouse.move(x, 250)
        page.mouse.wheel(0, 250)
        page.wait_for_timeout(250)
        assert top() > 0, f"wheel at x={x} did not scroll"
    ctx.close()


# ---------------------------------------------------------------------------
# Real server, real reconnects.
# ---------------------------------------------------------------------------

def _two_players(browser, base_url):
    alice = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    bob_ctx = browser.new_context(viewport={"width": 390, "height": 844})
    bob = bob_ctx.new_page()
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
    alice.click("#start-game-btn")
    for pg in (alice, bob):
        expect(pg.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    return alice, bob, bob_ctx, room


def test_dropped_socket_reconnects_into_the_running_game(browser, base_url):
    alice, bob, _, _ = _two_players(browser, base_url)
    bob.evaluate("window.__app.socket._ws.close()")                       # the phone lost the connection
    expect(bob.locator("#connection-banner")).to_be_hidden(timeout=8000)  # back, without a manual step
    expect(bob.locator("#vydrz-panel")).to_be_visible()
    expect(bob.locator("#vy-locations .vy-slot")).to_have_count(8)
    expect(alice.locator("#vydrz-panel")).to_be_visible()


def test_page_reload_returns_to_the_game(browser, base_url):
    alice, bob, _, _ = _two_players(browser, base_url)
    bob.reload()
    expect(bob.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    expect(bob.locator("#vy-locations .vy-slot")).to_have_count(8)
    expect(bob.locator("#vy-round")).to_have_text("Kolo 1 / 8")


def test_coming_back_with_a_new_tab_under_the_same_name_works_and_strangers_get_a_message(browser, base_url):
    alice, bob, bob_ctx, room = _two_players(browser, base_url)
    bob_ctx.close()                                                      # the whole browser went away
    expect(alice.locator("#vy-players [data-pid]", has_text="odpojen")).to_have_count(1, timeout=8000)

    again = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    again.goto(base_url)
    again.fill("#display-name-input", "bob")
    again.fill("#room-id-input", room)
    again.click("#join-btn")
    expect(again.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    expect(again.locator("#vy-locations .vy-slot")).to_have_count(8)

    stranger = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    stranger.goto(base_url)
    stranger.fill("#display-name-input", "Cara")
    stranger.fill("#room-id-input", room)
    stranger.click("#join-btn")
    expect(stranger.locator(".form-error")).to_contain_text("Hra už běží", timeout=8000)
    expect(stranger.locator("#join-panel")).to_be_visible()


# ---------------------------------------------------------------------------
# Desktop: the card back is seen during the deal, and animation frames stay cheap.
# ---------------------------------------------------------------------------

def test_desktop_deal_shows_the_card_back_before_flipping(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script("localStorage.setItem('baron.settings', JSON.stringify({deal: true}))")
    page = ctx.new_page()
    page.goto(f"{base_url}?mock=table&bots=2")
    back = page.locator(".di__card:not(.is-flipped) .di__face--back").first
    expect(back).to_be_visible(timeout=8000)
    box = back.bounding_box()
    assert box and box["width"] > 40 and box["height"] > 60
    ctx.close()


def test_desktop_frames_stay_cheap_while_the_result_counts(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    _table(page, base_url)
    page.locator("#vy-locations .vy-slot").first.click()
    page.locator("#vy-existences .vy-slot").first.click()
    page.click('[data-act="confirm"]')
    expect(page.locator(".vr__panel")).to_be_visible(timeout=15000)
    median = page.evaluate("""() => new Promise((resolve) => {
        const dts = []; let last = performance.now();
        const tick = (t) => { dts.push(t - last); last = t; if (dts.length < 60) requestAnimationFrame(tick);
          else { dts.sort((a, b) => a - b); resolve(dts[Math.floor(dts.length / 2)]); } };
        requestAnimationFrame(tick);
    })""")
    # A full-screen backdrop-filter used to push this to ~57 ms in software rendering.
    assert median < 34, f"median frame {median:.1f} ms"
    ctx.close()
