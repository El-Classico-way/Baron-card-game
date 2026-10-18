"""Opening deal (both Výdrž variants): the 16 cards are revealed pair by pair."""
import pytest
from playwright.sync_api import expect

MODES = {"automatic": ("vydrz", "#vydrz-panel", "#vy-locations .vy-slot", "#vy-existences .vy-slot"),
         "manual": ("vydrz_rucne", "#vm-panel", '.vy-slot[data-kind="lokace"]', '.vy-slot[data-kind="existence"]')}


@pytest.fixture(params=MODES)
def dealt_page(request, browser, base_url):
    mode, panel, locs, exs = MODES[request.param]
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.add_init_script("localStorage.setItem('baron.settings', JSON.stringify({deal: true, sound: false}))")
    page = ctx.new_page()
    page.js_errors = []
    page.on("pageerror", lambda e: page.js_errors.append(str(e)))
    page.goto(f"{base_url}?mock=table&mode={mode}&bots=2")
    yield page, panel, locs, exs
    ctx.close()


def _landed(page, locs, exs):
    return page.locator(f"{locs}:not(.is-undealt), {exs}:not(.is-undealt)").count()


def test_cards_are_dealt_one_pair_at_a_time(dealt_page):
    page, panel, locs, exs = dealt_page
    expect(page.locator("#deal-intro")).to_be_visible(timeout=10000)
    expect(page.locator(".di__pile")).to_have_count(2)                   # the decks are on screen

    counts, max_on_screen, seen = set(), 0, set()
    for _ in range(80):                                                  # sample the animation
        if page.locator("#deal-intro").is_hidden():
            break
        seen.add(page.locator(".di__count").inner_text())
        max_on_screen = max(max_on_screen, page.locator(".di__card").count())
        counts.add(_landed(page, locs, exs))
        page.wait_for_timeout(150)
    assert max_on_screen <= 2, "only the current pair may be on the stage"
    assert {"Dvojice 1 / 8", "Dvojice 4 / 8"} <= seen or len(seen) >= 6, seen
    assert len(counts) >= 4, f"cards should appear gradually, saw {sorted(counts)}"
    assert min(counts) <= 2

    expect(page.locator("#deal-intro")).to_be_hidden(timeout=20000)
    assert page.locator(locs).count() == 8 and page.locator(exs).count() == 8
    assert _landed(page, locs, exs) == 16                                # all 16 are in the hand
    assert page.locator(".is-undealt").count() == 0
    assert not page.js_errors


def test_skip_deals_everything_at_once(dealt_page):
    page, panel, locs, exs = dealt_page
    expect(page.locator("#deal-intro")).to_be_visible(timeout=10000)
    page.click(".di__skip")
    expect(page.locator("#deal-intro")).to_be_hidden(timeout=5000)
    assert _landed(page, locs, exs) == 16
    expect(page.locator(panel)).to_be_visible()


def test_deal_can_be_switched_off_in_settings(browser, base_url):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    # set once, so that the value changed in the settings sheet survives the reload
    ctx.add_init_script("if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); "
                        "localStorage.setItem('baron.settings', JSON.stringify({deal: true})); }")
    page = ctx.new_page()
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#deal-intro")).to_be_visible(timeout=10000)
    page.click(".di__skip")
    page.click("#vy-settings-btn")
    page.click('[data-set-deal="off"]')
    assert page.evaluate("JSON.parse(localStorage.getItem('baron.settings')).deal") is False
    page.reload()
    expect(page.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    page.wait_for_timeout(800)
    assert page.locator("#deal-intro").is_hidden()
    ctx.close()
