"""Výdrž (ruční): lobby variant, planning all 8 pairs, drink reveal, number pad,
Blbec / Švindlíř tags, and two real browsers on the server."""
import pytest
from playwright.sync_api import expect

VIEWPORTS = {
    "phone": dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True),
    "small-phone": dict(viewport={"width": 320, "height": 568}, is_mobile=True, has_touch=True),
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


def _manual_table(page, base_url, bots=2):
    page.goto(f"{base_url}?mock=table&mode=vydrz_rucne&bots={bots}")
    expect(page.locator("#vm-panel")).to_be_visible(timeout=8000)


def _plan_all(page):
    for _ in range(8):
        page.locator('.vy-slot[data-kind="lokace"]').first.click()
        page.locator('.vy-slot[data-kind="existence"]').first.click()
        page.click('[data-act="add"]') if page.locator('[data-act="add"]').count() else None


def _wait_for_entry_open(page):
    page.wait_for_function("document.querySelector('[data-field]') && !document.querySelector('[data-field]').disabled", timeout=20000)


def _correct(page):
    """The right answer, read from the mock engine (dev hook on 127.0.0.1)."""
    return page.evaluate("window.__app.socket._vydrz.calc.correct.me")


def _enter(page, value):
    page.click("[data-field]")
    expect(page.locator(".np__panel")).to_be_visible()
    if value < 0:
        page.click('[data-key="sign"]')
    for ch in str(abs(value)):
        page.click(f'[data-key="{ch}"]')
    page.click('[data-key="enter"]')
    expect(page.locator(".np__panel")).to_have_count(0)


def _no_hscroll(page):
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0


# ---------------------------------------------------------------------------

def test_lobby_offers_automatic_and_manual_variants(page, base_url):
    page.goto(f"{base_url}?mock=1&bots=2")
    expect(page.locator("#lobby-panel")).to_be_visible()
    expect(page.locator("#variant-box")).to_be_visible(timeout=5000)
    auto, manual = page.locator("#variant-switch .seg__btn").nth(0), page.locator("#variant-switch .seg__btn").nth(1)
    expect(auto).to_have_text("Automaticky")
    expect(auto).to_have_class("seg__btn is-on")
    page.click('#variant-switch [data-mode="vydrz_rucne"]')
    expect(manual).to_have_class("seg__btn is-on")
    expect(page.locator("#variant-hint")).to_contain_text("naplánuješ")
    _no_hscroll(page)


def test_planning_needs_all_eight_pairs(page, base_url):
    _manual_table(page, base_url)
    add = page.locator('[data-act="add"]')
    expect(add).to_be_disabled()
    for i in range(7):
        page.locator('.vy-slot[data-kind="lokace"]').first.click()
        page.locator('.vy-slot[data-kind="existence"]').first.click()
        expect(add).to_be_enabled()
        add.click()
        expect(page.locator(".vm-plan__list li.is-set")).to_have_count(i + 1)
    # With one card of each kind left there is nothing to choose: both are pre-selected.
    expect(page.locator(".vy-slot.is-selected")).to_have_count(2)
    expect(add).to_be_enabled()
    add.click()
    expect(page.locator('[data-act="confirm"]')).to_be_visible()
    expect(page.locator(".vm-plan__list li.is-set")).to_have_count(8)
    page.click('[data-act="undo"]')
    expect(page.locator(".vm-plan__list li.is-set")).to_have_count(7)
    _no_hscroll(page)
    assert not page.js_errors


def test_calculating_shows_pair_drinks_and_numpad(page, base_url):
    _manual_table(page, base_url)
    _plan_all(page)
    page.click('[data-act="confirm"]')

    # Three drink cards are turned over one by one, then the field unlocks.
    expect(page.locator(".vm-drinks__cards")).to_be_visible(timeout=15000)
    page.wait_for_function("document.querySelectorAll('.vm-drinks__cards .vc--chlast[data-drink]').length >= 1", timeout=15000)
    page.wait_for_function("document.querySelectorAll('.vm-drinks__cards .vc--chlast[data-drink]').length === 3", timeout=15000)
    _wait_for_entry_open(page)

    # My character, location and existence are all on screen with their stats.
    expect(page.locator(".vm-mine .vc--postava .vc-stat")).to_have_count(10)
    expect(page.locator(".vm-mine .vc--lokace .vc-stat").first).to_be_visible()
    expect(page.locator(".vm-mine .vc--existence .vc-stat").first).to_be_visible()
    # They can be enlarged.
    page.locator(".vm-mine .vc--lokace").click()
    expect(page.locator("#card-inspector")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#card-inspector")).to_be_hidden()

    # The number pad replaces the keyboard: nothing editable gets focus.
    page.click("[data-field]")
    expect(page.locator(".np__panel")).to_be_visible()
    assert page.locator(".np__panel input").count() == 0
    assert page.evaluate("!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)")
    box = page.locator(".np__key--enter").bounding_box()
    assert box["height"] >= 44 and box["y"] + box["height"] <= page.viewport_size["height"] + 1
    page.click('[data-key="4"]'); page.click('[data-key="2"]'); page.click('[data-key="sign"]')
    expect(page.locator("#np-display")).to_have_text("−42")
    page.click('[data-key="back"]')
    expect(page.locator("#np-display")).to_have_text("−4")
    page.click('[data-key="back"]'); page.click('[data-key="back"]')
    expect(page.locator("#np-display")).to_have_text("–")                  # empty
    page.click('[data-key="enter"]')
    expect(page.locator(".np__panel")).to_be_visible()                    # Enter does nothing while empty
    page.click(".np__panel [data-close]")
    expect(page.locator(".np__panel")).to_have_count(0)
    assert not page.js_errors


def test_correct_low_and_high_entries_show_the_right_tags(page, base_url):
    _manual_table(page, base_url, bots=1)
    _plan_all(page)
    page.click('[data-act="confirm"]')

    # Round 1: lower than the right number -> value as typed + "Blbec".
    _wait_for_entry_open(page)
    right = _correct(page)
    _enter(page, right - 2)
    me_row = page.locator(".vm-row", has_text="You")
    expect(me_row.locator(".vm-tag--blbec")).to_have_text("Blbec")
    expect(page.locator(".vm-field")).to_contain_text("zadáno")
    expect(page.locator("[data-field]")).to_be_disabled()                  # one entry only
    expect(page.locator("#vm-bar button[data-act='next']")).to_be_visible(timeout=20000)
    expect(page.locator(".vm-row.is-winner, .vr__outcome").first).to_be_visible()
    expect(page.locator(".vm-row__right").first).to_contain_text("správně")   # the right numbers are shown afterwards
    page.click('[data-act="next"]')

    # Round 2: higher -> "Švindlíř", counted as 0.
    _wait_for_entry_open(page)
    right = _correct(page)
    _enter(page, right + 3)
    row = page.locator(".vm-row", has_text="You")
    expect(row.locator(".vm-tag--svindlir")).to_have_text("Švindlíř")
    expect(row.locator(".vm-row__val")).to_have_text("0")
    expect(page.locator('[data-act="next"]')).to_be_visible(timeout=20000)
    page.click('[data-act="next"]')

    # Round 3: exactly right -> no tag, the plain number.
    _wait_for_entry_open(page)
    right = _correct(page)
    _enter(page, right)
    row = page.locator(".vm-row", has_text="You")
    expect(row.locator(".vm-row__val")).to_have_text(str(right).replace("-", "−"))
    assert row.locator(".vm-tag").count() == 0
    assert not page.js_errors


def test_keyboard_works_on_the_number_pad(page, base_url):
    if page.viewport_size["width"] < 900:
        pytest.skip("physical keyboard is a desktop feature")
    _manual_table(page, base_url, bots=1)
    _plan_all(page)
    page.click('[data-act="confirm"]')
    _wait_for_entry_open(page)
    right = _correct(page)
    page.click("[data-field]")
    typed = str(right)
    for ch in typed:
        page.keyboard.press("Minus" if ch == "-" else ch)
    expect(page.locator("#np-display")).to_have_text(typed.replace("-", "−"))
    page.keyboard.press("Enter")
    expect(page.locator(".np__panel")).to_have_count(0)
    expect(page.locator(".vm-row", has_text="You").locator(".vm-row__val")).to_have_text(typed.replace("-", "−"))
    assert page.locator(".vm-row", has_text="You").locator(".vm-tag").count() == 0     # typed the right number


def test_full_manual_game_reaches_the_final_ranking(page, base_url):
    if page.viewport_size["width"] != 390:
        pytest.skip("long flow runs on one viewport only")
    _manual_table(page, base_url, bots=1)
    _plan_all(page)
    page.click('[data-act="confirm"]')
    for rnd in range(1, 9):
        guard = 0
        while guard < 12:
            guard += 1
            page.wait_for_function(
                "document.querySelector('[data-field]:not([disabled])') || document.querySelector('[data-act=\"next\"]') || document.querySelector('[data-act=\"leave\"]')",
                timeout=30000)
            if page.locator("[data-field]:not([disabled])").count():
                _enter(page, _correct(page))                    # tie-breaks are counted by hand too
            else:
                break
        if rnd < 8:
            page.click('[data-act="next"]')
    expect(page.locator(".vr__ranking li")).to_have_count(2)
    expect(page.locator(".vr__ranking li.is-winner")).to_have_count(1)
    assert not page.js_errors


# ---------------------------------------------------------------------------
# Two real browsers against the real server.
# ---------------------------------------------------------------------------

def test_two_browsers_play_a_manual_round_on_the_real_server(browser, base_url):
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

    alice.click('#variant-switch [data-mode="vydrz_rucne"]')
    expect(bob.locator("#variant-switch .is-on")).to_have_text("Ručně")
    alice.click("#start-game-btn")

    for pg in (alice, bob):
        expect(pg.locator("#vm-panel")).to_be_visible(timeout=8000)
        _plan_all(pg)
        pg.click('[data-act="confirm"]')

    for pg in (alice, bob):
        pg.wait_for_function("document.querySelectorAll('.vm-drinks__cards .vc--chlast[data-drink]').length === 3", timeout=20000)
        _wait_for_entry_open(pg)

    # Alice cheats (far too high -> counts 0), Bob types a far too low number (counts as typed).
    _enter(alice, 150)
    expect(bob.locator(".vm-row", has_text="Alice").locator(".vm-tag--svindlir")).to_be_visible(timeout=8000)   # public at once
    assert bob.locator(".vm-row", has_text="Bob").locator(".vm-row__val").inner_text() == "počítá…"
    _enter(bob, -99)

    for pg in (alice, bob):
        expect(pg.locator(".vm-banner")).to_contain_text("výsledek", timeout=8000)
        expect(pg.locator(".vm-row", has_text="Alice").locator(".vm-tag--svindlir")).to_be_visible()
        expect(pg.locator(".vm-row", has_text="Bob").locator(".vm-tag--blbec")).to_be_visible()
    expect(alice.locator(".vr__outcome")).to_contain_text("Alice")        # 0 beats -99
    expect(bob.locator(".vr__waiting")).to_be_visible()                    # only the host continues
    alice.click('[data-act="next"]')
    for pg in (alice, bob):
        expect(pg.locator("#vm-title")).to_have_text("Kolo 2 / 8")
