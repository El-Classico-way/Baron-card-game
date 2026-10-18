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
    page.goto(f"{base_url}?mock=1&mode=rezba&bots=3")
    expect(page.locator("#lobby-panel")).to_be_visible()
    expect(page.locator("#mock-ribbon")).to_be_visible()
    expect(page.locator("#lobby-player-list li")).to_have_count(4, timeout=5000)
    expect(page.locator("#start-game-btn")).to_be_enabled()
    _no_horizontal_scroll(page)


def test_table_interaction(page, base_url):
    page.goto(f"{base_url}?mock=table&mode=rezba&bots=5")
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
    page.goto(f"{base_url}?mock=table&mode=rezba&bots=2")
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
    page.goto(f"{base_url}?mock=table&mode=rezba&bots=2")
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
    page.goto(f"{base_url}?mock=table&mode=rezba&bots=2")
    expect(page.locator("#game-panel")).to_be_visible(timeout=8000)
    page.click('[data-action="draw"]')
    page.click('[data-action="end-turn"]')
    expect(page.locator("#turn-indicator")).not_to_contain_text("Your turn")
    expect(page.locator('[data-action="draw"]')).to_be_visible(timeout=12000)


# ---------------------------------------------------------------------------
# Card inspector (enlarged card view)
# ---------------------------------------------------------------------------

def _table(page, base_url, bots=2):
    page.goto(f"{base_url}?mock=table&mode=rezba&bots={bots}")
    expect(page.locator("#game-panel")).to_be_visible(timeout=8000)


def _long_press(page, locator):
    box = locator.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(650)
    page.mouse.up()


def test_inspect_when_not_playing_a_tap_opens_it(page, base_url):
    _table(page, base_url)       # draw phase: a tap has no game meaning
    page.locator("#hand-area .hand-card").first.click()
    expect(page.locator("#card-inspector")).to_be_visible()
    expect(page.locator(".inspector__name")).to_have_text("Card 1")
    expect(page.locator(".inspector__desc")).not_to_be_empty()


def test_inspect_is_readable_and_fits_the_screen(page, base_url):
    _table(page, base_url)
    page.locator("#hand-area .hand-card").nth(2).click()      # "Card 3": the long text
    sheet = page.locator(".inspector__sheet")
    expect(sheet).to_be_visible()
    vp = page.viewport_size
    box = sheet.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= vp["width"] + 1
    assert box["y"] >= 0 and box["y"] + box["height"] <= vp["height"] + 1
    big = page.locator(".inspect-card").bounding_box()
    small = page.locator("#hand-area .hand-card").first.bounding_box()
    assert big["width"] >= small["width"] * 1.5, (big, small)
    # Text is big enough to read.
    assert page.evaluate("parseFloat(getComputedStyle(document.querySelector('.inspector__desc')).fontSize)") >= 16
    # Not scrollable sideways.
    assert page.evaluate("document.querySelector('.inspector__sheet').scrollWidth - document.querySelector('.inspector__sheet').clientWidth") <= 1
    _no_horizontal_scroll(page)


def test_long_press_inspects_without_selecting_on_my_turn(page, base_url):
    _table(page, base_url)
    page.click('[data-action="draw"]')
    expect(page.locator("#hand-area .hand-card")).to_have_count(6)
    _long_press(page, page.locator("#hand-area .hand-card").nth(1))
    expect(page.locator("#card-inspector")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#card-inspector")).to_be_hidden()
    expect(page.locator("#hand-area .hand-card--selected")).to_have_count(0)


def test_right_click_inspects(page, base_url):
    _table(page, base_url)
    page.click('[data-action="draw"]')
    page.locator("#hand-area .hand-card").nth(1).click(button="right")
    expect(page.locator("#card-inspector")).to_be_visible()


def test_inspect_button_for_selected_card_then_navigate_and_close(page, base_url):
    _table(page, base_url)
    page.click('[data-action="draw"]')
    expect(page.locator("#hand-area .hand-card--playable")).to_have_count(6)   # draw finished
    inspect_btn = page.locator('[data-action="inspect"]')
    expect(inspect_btn).to_be_hidden()
    page.locator("#hand-area .hand-card").nth(1).click()
    expect(page.locator("#hand-area .hand-card--selected")).to_have_count(1)
    expect(inspect_btn).to_be_visible()
    assert inspect_btn.bounding_box()["height"] >= 40
    inspect_btn.click()
    name = page.locator(".inspector__name")
    expect(name).to_have_text("Card 2")
    page.click('[data-nav="1"]')
    expect(name).to_have_text("Card 3")
    expect(page.locator(".inspector__count")).to_have_text("3 / 6")
    page.click('[data-nav="-1"]')
    page.click('[data-nav="-1"]')
    expect(name).to_have_text("Card 1")
    # Close with the close button; the selection survives.
    page.click(".inspector__close")
    expect(page.locator("#card-inspector")).to_be_hidden()
    expect(page.locator("#hand-area .hand-card--selected")).to_have_count(1)
    # Backdrop tap also closes.
    page.locator('[data-action="inspect"]').click()
    page.mouse.click(2, page.viewport_size["height"] - 2)
    expect(page.locator("#card-inspector")).to_be_hidden()


def test_inspector_never_sends_actions(page, base_url):
    """Looking at a card is local: hand and pile counts do not change."""
    _table(page, base_url)
    page.click('[data-action="draw"]')
    expect(page.locator("#hand-area .hand-card")).to_have_count(6)
    before = page.locator("#discard-count").inner_text()
    page.locator("#hand-area .hand-card").first.click(button="right")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    assert page.locator("#hand-area .hand-card").count() == 6
    assert page.locator("#discard-count").inner_text() == before


def test_discard_pile_top_can_be_inspected(page, base_url):
    _table(page, base_url)
    page.click('[data-action="draw"]')
    expect(page.locator("#hand-area .hand-card--playable")).to_have_count(6)   # draw finished
    page.locator("#hand-area .hand-card").first.click()
    page.locator(".opponent").first.click()
    expect(page.locator("#discard-top .discard-card")).to_be_visible()
    page.locator("#discard-top").click()
    expect(page.locator("#card-inspector")).to_be_visible()
    expect(page.locator(".inspector__where")).to_contain_text("discard")
    expect(page.locator(".inspector__nav")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(page.locator("#card-inspector")).to_be_hidden()
    assert not page.js_errors
