"""Výdrž UI: mode switch, table layout, picking, result overlay, full mock game,
and two real browsers playing a round through the server."""
import pytest
from playwright.sync_api import expect

VIEWPORTS = {
    "phone": dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True),
    "small-phone": dict(viewport={"width": 320, "height": 568}, is_mobile=True, has_touch=True),
    "landscape": dict(viewport={"width": 844, "height": 390}, is_mobile=True, has_touch=True),
    "desktop": dict(viewport={"width": 1280, "height": 800}),
}


@pytest.fixture(params=VIEWPORTS)
def page(request, browser):
    ctx = browser.new_context(**VIEWPORTS[request.param])
    pg = ctx.new_page()
    pg.js_errors = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    yield pg
    ctx.close()


def _table(page, base_url, bots=3):
    page.goto(f"{base_url}?mock=table&bots={bots}")
    expect(page.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    expect(page.locator("#vy-locations .vy-slot")).to_have_count(8)


def _pick_and_confirm(page, li=0, ei=0):
    page.locator("#vy-locations .vy-slot").nth(li).click()
    page.locator("#vy-existences .vy-slot").nth(ei).click()
    page.click('[data-act="confirm"]')


def _no_hscroll(page):
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0


# ---------------------------------------------------------------------------

def test_mode_switch_in_lobby(page, base_url):
    page.goto(f"{base_url}?mock=1&bots=2")
    expect(page.locator("#lobby-panel")).to_be_visible()
    modes = page.locator("#mode-switch .seg__btn")
    expect(modes).to_have_count(2, timeout=5000)
    rezba, vydrz = modes.nth(0), modes.nth(1)
    expect(rezba).to_contain_text("Řežba")
    expect(rezba).to_be_disabled()                    # not implemented yet
    expect(vydrz).to_have_class("seg__btn is-on")
    _no_hscroll(page)


def test_table_layout_and_card_readability(page, base_url):
    _table(page, base_url, bots=6)                    # 7 players
    _no_hscroll(page)
    # The three inspectable decks and the drink deck are on screen, with their counts.
    for kind in ("postava", "lokace", "existence"):
        expect(page.locator(f'.vy-pile[data-pile="{kind}"] .vc--back')).to_be_visible()
    expect(page.locator(".vy-drawn__deck .vc--back-chlast")).to_be_visible()
    # Hand: 8 locations and 8 existences, each with 2-3 stat chips showing values.
    expect(page.locator("#vy-existences .vy-slot")).to_have_count(8)
    chips = page.locator("#vy-locations .vy-slot").first.locator(".vc-stat")
    assert 2 <= chips.count() <= 3
    # Character card shows ten stats.
    assert page.locator("#vy-me .vc--postava .vc-stat").count() == 10
    # Stat text is legible (>= 10px) and the confirm bar fits the screen.
    size = page.evaluate("parseFloat(getComputedStyle(document.querySelector('#vy-me .vc-stat')).fontSize)")
    assert size >= 10, size
    box = page.locator("#vy-bar").bounding_box()
    assert box["y"] + box["height"] <= page.viewport_size["height"] + 1
    assert not page.js_errors


def test_pick_confirm_and_wait_state(page, base_url):
    _table(page, base_url)
    confirm = page.locator('[data-act="confirm"]')
    expect(confirm).to_be_disabled()
    page.locator("#vy-locations .vy-slot").nth(2).click()
    expect(confirm).to_be_disabled()                  # needs both
    page.locator("#vy-existences .vy-slot").nth(1).click()
    expect(confirm).to_be_enabled()
    assert confirm.bounding_box()["height"] >= 44
    expect(page.locator("#vy-bar .vy-pick b").first).not_to_have_text("—")
    confirm.click()
    # Bots pick within a couple of seconds, which resolves the round -> overlay.
    expect(page.locator(".vr__panel")).to_be_visible(timeout=15000)


def test_inspect_card_shows_stats_big(page, base_url):
    _table(page, base_url)
    page.locator("#vy-locations .vy-slot").nth(3).click()
    page.locator("#vy-locations .vy-slot.is-selected [data-inspect]").click()
    expect(page.locator("#card-inspector")).to_be_visible()
    expect(page.locator(".inspector__name")).to_contain_text("Lokace 4")
    big = page.locator("#card-inspector .vc--lokace").bounding_box()
    small = page.locator("#vy-locations .vc--lokace").first.bounding_box()
    assert big["width"] > small["width"]
    assert 2 <= page.locator("#card-inspector .vc-stat").count() <= 3
    page.keyboard.press("Escape")
    expect(page.locator("#card-inspector")).to_be_hidden()
    # Character card opens too.
    page.locator("#vy-me").click()
    expect(page.locator("#card-inspector .vc--postava .vc-stat")).to_have_count(10)


def test_player_sheet_shows_public_hand(page, base_url):
    _table(page, base_url)
    page.locator("#vy-players [data-pid='bot1']").click()
    expect(page.locator("#vy-sheet")).to_be_visible()
    expect(page.locator("#vy-sheet .vc--lokace")).to_have_count(8)
    expect(page.locator("#vy-sheet .vc--existence")).to_have_count(8)
    page.keyboard.press("Escape")
    expect(page.locator("#vy-sheet")).to_be_hidden()


def test_result_overlay_counts_and_continues(page, base_url):
    _table(page, base_url)
    _pick_and_confirm(page)
    expect(page.locator(".vr__panel")).to_be_visible(timeout=15000)
    # Numbers appear while animating (a cell gets filled), then the outcome and Next.
    expect(page.locator(".vr__cell.is-hit, .vr__cell.is-miss").first).to_be_visible(timeout=10000)
    expect(page.locator(".vr__next")).to_be_visible(timeout=25000)
    expect(page.locator(".vr__outcome")).to_be_visible()
    _no_hscroll(page)
    page.click(".vr__next")
    expect(page.locator(".vr__panel")).to_have_count(0)
    expect(page.locator("#vy-round")).to_have_text("Kolo 2 / 8")
    expect(page.locator("#vy-locations .vy-slot")).to_have_count(7)    # the played location left the hand


def test_skip_jumps_to_the_end(page, base_url):
    _table(page, base_url)
    _pick_and_confirm(page)
    expect(page.locator(".vr__skip")).to_be_visible(timeout=15000)
    page.click(".vr__skip")
    expect(page.locator(".vr__next")).to_be_visible(timeout=3000)


def test_full_mock_game_to_the_end(page, base_url):
    _table(page, base_url, bots=2)
    for rnd in range(1, 9):
        expect(page.locator("#vy-round")).to_have_text(f"Kolo {rnd} / 8", timeout=15000)
        _pick_and_confirm(page)
        expect(page.locator(".vr__skip")).to_be_visible(timeout=15000)
        page.click(".vr__skip")
        if rnd < 8:
            page.click(".vr__next")
    # The last round can be read first; the winner appears only when the host announces it.
    expect(page.locator(".vr__announce")).to_be_visible(timeout=5000)
    expect(page.locator("#baron-splash")).to_be_hidden()
    expect(page.locator(".vr__final")).to_be_hidden()
    page.click(".vr__announce")
    expect(page.locator("#baron-splash")).to_be_visible(timeout=10000)        # "<Jméno> je Baron!" for everyone
    winner = page.locator("#baron-splash .bs__name").inner_text()
    expect(page.locator("#baron-splash .bs__title")).to_contain_text("je Baron!")
    page.keyboard.press("Enter")
    expect(page.locator("#baron-splash")).to_be_hidden()
    expect(page.locator(".vr__final")).to_be_visible()
    assert winner in page.locator(".vr__ranking li.is-winner").inner_text()   # the splash named the ranking's winner
    expect(page.locator(".vr__ranking li")).to_have_count(3)
    expect(page.locator(".vr__ranking li.is-winner")).to_have_count(1)
    expect(page.locator(".vr__ranking li.is-loser")).to_have_count(1)
    expect(page.locator(".vr__lobby")).to_be_enabled()                         # the host can send everybody back
    assert not page.js_errors


def test_settings_sheet_sound_and_speed(page, base_url):
    _table(page, base_url)
    page.click("#vy-settings-btn")
    expect(page.locator("#vy-settings")).to_be_visible()
    speeds = page.locator("#vy-settings [data-set-speed]")
    expect(speeds).to_have_count(4)                                 # fastest (current pace) + three slower
    expect(speeds.nth(0)).to_have_class("seg__btn is-on")           # the existing pace stays the default
    expect(speeds.nth(0)).to_have_text("Nejrychlejší")
    page.click('[data-set-speed="3"]')
    expect(page.locator('[data-set-speed="3"]')).to_have_class("seg__btn is-on")
    assert page.evaluate("JSON.parse(localStorage.getItem('baron.settings')).speed") == 3
    page.click('[data-set-sound="off"]')
    expect(page.locator('[data-set-sound="off"]')).to_have_class("seg__btn is-on")
    assert page.evaluate("JSON.parse(localStorage.getItem('baron.settings')).sound") is False
    page.click("#vy-settings button[data-close]")
    expect(page.locator("#vy-settings")).to_be_hidden()


def test_slower_speed_stretches_the_counting(page, base_url):
    """Speed 4 is slower than speed 1: the animation takes clearly longer to reach the end."""
    import time
    if page.viewport_size["width"] != 390:
        pytest.skip("timing check runs on one viewport only")
    durations = {}
    for speed in (1, 4):
        _table(page, base_url, bots=1)
        page.evaluate(f"localStorage.setItem('baron.settings', JSON.stringify({{sound: false, speed: {speed}, deal: false}}))")
        page.reload()
        expect(page.locator("#vydrz-panel")).to_be_visible(timeout=8000)
        _pick_and_confirm(page)
        expect(page.locator(".vr__panel")).to_be_visible(timeout=15000)
        t0 = time.time()
        expect(page.locator(".vr__next")).to_be_visible(timeout=60000)
        durations[speed] = time.time() - t0
        assert page.evaluate("getComputedStyle(document.getElementById('vy-result')).getPropertyValue('--vr-speed')").strip() == str({1: 1, 4: 4}[speed])
    assert durations[4] > durations[1] * 2, durations


# ---------------------------------------------------------------------------
# Two real browsers against the real server (no mock).
# ---------------------------------------------------------------------------

def test_two_browsers_play_a_vydrz_round_on_the_real_server(browser, base_url):
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

    # Host sees the mode switch enabled for Výdrž; Bob's is read-only.
    expect(alice.locator('#mode-switch [data-mode="vydrz"]')).to_be_enabled()
    expect(bob.locator('#mode-switch [data-mode="vydrz"]')).to_be_disabled()
    alice.click('#mode-switch [data-mode="vydrz"]')
    alice.click("#start-game-btn")

    for pg in (alice, bob):
        expect(pg.locator("#vydrz-panel")).to_be_visible(timeout=8000)
        expect(pg.locator("#vy-locations .vy-slot")).to_have_count(8)
        expect(pg.locator("#vy-existences .vy-slot")).to_have_count(8)

    # Hands are public: Alice can open Bob's cards.
    alice.locator("#vy-players [data-pid]:not(.is-me)").click()
    expect(alice.locator("#vy-sheet .vc--lokace")).to_have_count(8)
    alice.keyboard.press("Escape")

    _pick_and_confirm(alice, 0, 0)
    expect(alice.locator(".vy-wait")).to_contain_text("1/2")
    _pick_and_confirm(bob, 1, 1)

    for pg in (alice, bob):
        expect(pg.locator(".vr__panel")).to_be_visible(timeout=8000)
        pg.click(".vr__skip")
        expect(pg.locator(".vr__outcome")).to_be_visible(timeout=3000)
    expect(bob.locator(".vr__waiting")).to_be_visible()          # only the host continues
    expect(alice.locator(".vr__next")).to_be_visible()
    alice.click(".vr__next")
    for pg in (alice, bob):
        expect(pg.locator("#vy-round")).to_have_text("Kolo 2 / 8")
