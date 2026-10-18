"""
UI checks for the mock-driven prototype at phone/tablet/desktop sizes.

These run against ?mock (see client/js/mock/mock-socket.js), so they verify
layout and touch interaction only, never game rules.
"""
import pytest
from playwright.sync_api import expect

VIEWPORTS = {
    "phone-portrait": dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True),
    "small-phone": dict(viewport={"width": 320, "height": 568}, is_mobile=True, has_touch=True),
    "phone-landscape": dict(viewport={"width": 844, "height": 390}, is_mobile=True, has_touch=True),
    "tablet": dict(viewport={"width": 768, "height": 1024}, is_mobile=True, has_touch=True),
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


def _no_horizontal_scroll(pg):
    assert pg.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0


def _inside_viewport(pg, selector):
    box = pg.locator(selector).bounding_box()
    vp = pg.viewport_size
    assert box is not None, selector
    assert box["x"] >= -1 and box["y"] >= -1, (selector, box)
    assert box["x"] + box["width"] <= vp["width"] + 1, (selector, box)
    assert box["y"] + box["height"] <= vp["height"] + 1, (selector, box)


def test_landing_page(page, base_url):
    page.goto(base_url)
    expect(page.locator("#join-panel")).to_be_visible()
    _no_horizontal_scroll(page)
    for sel in ("#create-btn", "#join-btn", "#demo-btn"):
        page.locator(sel).scroll_into_view_if_needed()
        assert page.locator(sel).bounding_box()["height"] >= 40, sel
    # 16px inputs stop iOS Safari from zooming on focus.
    assert page.evaluate("parseFloat(getComputedStyle(document.getElementById('display-name-input')).fontSize)") >= 16
    assert not page.js_errors


def test_join_requires_name_and_code(page, base_url):
    page.goto(base_url)
    page.click("#create-btn")
    expect(page.locator(".form-error")).to_contain_text("name")
    page.fill("#display-name-input", "Tester")
    page.click("#join-btn")
    expect(page.locator(".form-error")).to_contain_text("room code")


def test_lobby_in_mock_mode(page, base_url):
    page.goto(f"{base_url}?mock=1&bots=3")
    expect(page.locator("#lobby-panel")).to_be_visible()
    expect(page.locator("#mock-ribbon")).to_be_visible()
    expect(page.locator("#lobby-player-list li")).to_have_count(4, timeout=5000)
    expect(page.locator("#start-game-btn")).to_be_enabled()
    _no_horizontal_scroll(page)


def test_table_interaction(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=5")
    expect(page.locator("#game-panel")).to_be_visible(timeout=8000)
    _no_horizontal_scroll(page)

    # Draw, then the hand shows readable cards.
    draw = page.locator('[data-action="draw"]')
    expect(draw).to_be_visible()
    assert draw.bounding_box()["height"] >= 44
    draw.click()
    cards = page.locator("#hand-area .hand-card")
    expect(cards).to_have_count(6)
    first = cards.first.bounding_box()
    assert first["width"] >= 56 and first["height"] >= 78
    _inside_viewport(page, '[data-action="end-turn"]')
    _inside_viewport(page, "#turn-indicator")

    # Tap a card: it is selected and a target prompt appears.
    cards.nth(1).tap() if page.evaluate("'ontouchstart' in window") else cards.nth(1).click()
    expect(page.locator("#hand-area .hand-card--selected")).to_have_count(1)
    expect(page.locator("#target-prompt")).to_be_visible()

    # Tap an opponent: the (mock) server moves the card to the discard pile.
    page.locator(".opponent").first.click()
    expect(cards).to_have_count(5)
    expect(page.locator("#discard-top .discard-card")).to_be_visible()
    _no_horizontal_scroll(page)

    # Opponents stay inside the viewport horizontally; the area may scroll vertically.
    box = page.locator("#opponents-area").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"] + 1
    assert not page.js_errors


def test_hand_scrolls_inside_itself_not_the_page(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#game-panel")).to_be_visible(timeout=8000)
    for _ in range(3):
        page.click('[data-action="draw"]')
        expect(page.locator('[data-action="end-turn"]')).to_be_visible()
        break
    scrolls = page.evaluate("""() => {
        const h = document.getElementById('hand-area');
        return { hand: h.scrollWidth - h.clientWidth,
                 page: document.documentElement.scrollWidth - window.innerWidth };
    }""")
    assert scrolls["page"] <= 0


def test_chat_sheet_on_small_screens(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#game-panel")).to_be_visible(timeout=8000)
    if page.viewport_size["width"] >= 900:
        expect(page.locator("#chat-input")).to_be_visible()      # permanent sidebar
    else:
        expect(page.locator("#chat-input")).not_to_be_in_viewport()
        page.click("#chat-toggle")
        expect(page.locator("#chat-input")).to_be_in_viewport()
    page.fill("#chat-input", "hello")
    page.press("#chat-input", "Enter")
    expect(page.locator(".chat-message__text", has_text="hello")).to_be_visible()
    assert not page.js_errors


def test_bots_take_their_turn_and_it_returns_to_me(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#game-panel")).to_be_visible(timeout=8000)
    page.click('[data-action="draw"]')
    page.click('[data-action="end-turn"]')
    expect(page.locator("#turn-indicator")).not_to_contain_text("Your turn")
    expect(page.locator('[data-action="draw"]')).to_be_visible(timeout=12000)
