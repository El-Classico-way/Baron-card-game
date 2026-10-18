"""Demo button, copy code, deck inspection, own cards under the count, manual reorder / minus / close results."""
import pytest
from playwright.sync_api import expect

from tests.e2e.test_vydrz_manual_ui import _manual_table, _plan_all, _wait_for_entry_open


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.js_errors = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    yield pg
    ctx.close()


def test_try_the_demo_button_opens_the_demo_lobby(page, base_url):
    page.goto(base_url)
    page.click("#demo-btn")
    expect(page.locator("#lobby-panel")).to_be_visible(timeout=5000)
    expect(page.locator("#mock-ribbon")).to_be_visible()
    expect(page.locator("#lobby-player-list li")).to_have_count(2, timeout=5000)
    assert not page.js_errors


def test_copy_code_puts_only_the_room_code_on_the_clipboard(page, base_url):
    page.goto(base_url)
    page.fill("#display-name-input", "Alice")
    page.click("#create-btn")
    expect(page.locator("#lobby-panel")).to_be_visible()
    code = page.locator("#room-id-display").inner_text().strip()
    expect(page.locator("#copy-link-btn")).to_have_text("Zkopírovat kód")
    page.click("#copy-link-btn")
    page.wait_for_function("navigator.clipboard.readText().then(t => t.length > 0)")
    assert page.evaluate("navigator.clipboard.readText()") == code


def test_decks_can_be_inspected_and_the_drink_deck_shows_counts(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    for kind in ("postava", "lokace", "existence"):
        n = int(page.locator(f'[data-pile="{kind}"] b').inner_text())
        left = page.evaluate(f"window.__app.socket._vydrz.snapshot('me').decks_cards.{kind}.length")
        assert n == left > 0
        page.click(f'[data-pile="{kind}"]')
        expect(page.locator("#card-inspector")).to_be_visible()
        expect(page.locator(f"#card-inspector .vc--{'postava' if kind == 'postava' else kind}")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#card-inspector")).to_be_hidden()
    # One drink card with the remaining count per kind; it is not inspectable.
    expect(page.locator(".vy-drawn__deck .vc--back-chlast")).to_have_count(1)
    expect(page.locator(".vy-drinks-left li")).to_have_count(10)
    total = sum(int(t) for t in page.locator(".vy-drinks-left b").all_inner_texts())
    assert total == 100
    page.click(".vy-drawn__deck")
    expect(page.locator("#card-inspector")).to_be_hidden()


def test_own_cards_are_shown_under_the_counting_table(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#vy-locations .vy-slot")).to_have_count(8)
    page.locator("#vy-locations .vy-slot").first.click()
    page.locator("#vy-existences .vy-slot").first.click()
    page.click('[data-act="confirm"]')
    expect(page.locator(".vr__mine")).to_be_visible(timeout=15000)
    expect(page.locator(".vr__mine .vc--postava")).to_have_count(1)
    expect(page.locator(".vr__mine .vc--lokace")).to_have_count(1)
    expect(page.locator(".vr__mine .vc--existence")).to_have_count(1)
    # While counting, the stat that matters for the current drink is highlighted on the cards.
    page.wait_for_selector(".vr__mine .vc-stat.is-hot", timeout=20000)


def test_inspected_character_shows_full_drink_names(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    page.locator("#vy-me").click()
    names = page.locator("#card-inspector .vc--postava .vc-stat__name").all_inner_texts()
    assert "Jahůdka" in names and "Slivka" in names and "Jäger" in names


def test_desktop_cards_are_larger(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#vy-locations .vy-slot")).to_have_count(8)
    assert page.locator("#vy-locations .vc").first.bounding_box()["width"] >= 160


# --- manual variant ---------------------------------------------------------

def test_manual_pairs_can_be_reordered_by_dragging(page, base_url):
    _manual_table(page, base_url, bots=1)
    for _ in range(3):
        page.locator('.vy-slot[data-kind="lokace"]').first.click()
        page.locator('.vy-slot[data-kind="existence"]').first.click()
        page.click('[data-act="add"]')
    before = page.locator(".vm-plan__list li.is-set .vm-plan__loc").all_inner_texts()
    handle = page.locator(".vm-plan__list li.is-set .vm-grab").first
    page.locator(".vm-plan__list").scroll_into_view_if_needed()
    box = handle.bounding_box()
    third = page.locator(".vm-plan__list li.is-set").nth(2).bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.mouse.down()
    page.mouse.move(box["x"] + 5, (box["y"] + third["y"]) / 2 + 20, steps=4)
    page.mouse.move(box["x"] + 5, third["y"] + third["height"] / 2, steps=4)
    page.mouse.up()
    after = page.locator(".vm-plan__list li.is-set .vm-plan__loc").all_inner_texts()
    assert after == [before[1], before[2], before[0]]
    expect(page.locator(".vm-plan__list li.is-set .vm-n").first).to_have_text("1")


def test_manual_minus_key_steps_down_like_the_button(page, base_url):
    _manual_table(page, base_url, bots=1)
    _plan_all(page)
    page.click('[data-act="confirm"]')
    _wait_for_entry_open(page)
    base = page.locator("[data-value]").inner_text()
    page.keyboard.press("-")
    page.keyboard.press("-")
    page.keyboard.press("+")
    expect(page.locator("[data-value]")).to_have_text("−1")
    assert base == "0"


def test_manual_results_can_be_closed_to_the_base_view(page, base_url):
    _manual_table(page, base_url, bots=1)
    _plan_all(page)
    page.click('[data-act="confirm"]')
    _wait_for_entry_open(page)
    page.evaluate("window.__app.socket._vydrz.submit('me', window.__app.socket._vydrz.calc.correct.me)")
    expect(page.locator(".vm-banner")).to_contain_text("výsledek", timeout=10000)
    page.click("[data-close-result]")
    expect(page.locator(".vm-banner")).to_have_count(0)
    expect(page.locator("#vm-stage .vc--postava")).to_have_count(1)
    expect(page.locator("#vm-stage .vy-slot")).to_have_count(7 * 2)
    page.click('[data-act="showresult"]')
    expect(page.locator(".vm-banner")).to_contain_text("výsledek")


def test_winner_splash_rays_are_on_their_own_layer_and_frames_stay_cheap(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    pg = ctx.new_page()
    pg.goto(f"{base_url}?mock=table&bots=2")
    expect(pg.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    pg.evaluate("void import('/js/vydrz/winner-splash.js').then(m => { m.showBaronSplash({ name: 'Alice', isMe: true }); })")
    expect(pg.locator("#baron-splash .bs__rays")).to_be_visible()
    assert pg.evaluate("getComputedStyle(document.querySelector('.bs__rays')).willChange") == "transform"
    # A rotating layer as big as the screen made frames cost 4x in software rendering.
    assert pg.evaluate("document.querySelector('.bs__rays').getBoundingClientRect().width") <= 1080 * 1.45
    median = pg.evaluate("""() => new Promise((resolve) => {
        const dts = []; let last = performance.now();
        const tick = (t) => { dts.push(t - last); last = t; if (dts.length < 90) requestAnimationFrame(tick);
          else { dts.sort((a, b) => a - b); resolve(dts[45]); } };
        requestAnimationFrame(tick);
    })""")
    assert median < 62, f"median frame {median:.1f} ms"
    ctx.close()


# ---------------------------------------------------------------------------
# Round 4
# ---------------------------------------------------------------------------

FREQ_SPY = """
window.__freq = [];
const sv = AudioParam.prototype.setValueAtTime, rp = AudioParam.prototype.exponentialRampToValueAtTime;
AudioParam.prototype.setValueAtTime = function (v, t) { if (v > 50) window.__freq.push(v); return sv.call(this, v, t); };
AudioParam.prototype.exponentialRampToValueAtTime = function (v, t) { if (v > 50) window.__freq.push(v); return rp.call(this, v, t); };
"""


def test_results_overlay_scrolls_with_the_wheel_anywhere_on_the_screen(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1366, "height": 600})
    pg = ctx.new_page()
    pg.goto(f"{base_url}?mock=table&bots=2")
    expect(pg.locator("#vy-locations .vy-slot")).to_have_count(8)
    pg.locator("#vy-locations .vy-slot").first.click()
    pg.locator("#vy-existences .vy-slot").first.click()
    pg.click('[data-act="confirm"]')
    expect(pg.locator(".vr__skip")).to_be_visible(timeout=15000)
    pg.click(".vr__skip")
    assert pg.evaluate("(r => r.scrollHeight > r.clientHeight)(document.getElementById('vy-result'))")
    for x in (30, 683, 1340):
        pg.evaluate("document.getElementById('vy-result').scrollTop = 0")
        pg.mouse.move(x, 300)
        pg.mouse.wheel(0, 300)
        pg.wait_for_timeout(250)
        assert pg.evaluate("document.getElementById('vy-result').scrollTop") > 0, f"no scroll at x={x}"
    ctx.close()


def test_auto_cards_can_be_inspected_in_every_state(page, base_url):
    page.goto(f"{base_url}?mock=table&bots=2")
    expect(page.locator("#vy-locations .vy-slot")).to_have_count(8)
    page.locator("#vy-locations .vy-slot").nth(2).dblclick()                 # a quick second tap looks at the card
    expect(page.locator("#card-inspector")).to_be_visible()
    page.keyboard.press("Escape")
    page.locator("#vy-locations .vy-slot").first.click()
    page.locator("#vy-existences .vy-slot").first.click()
    page.click('[data-act="confirm"]')
    expect(page.locator('[data-act="change"]')).to_be_visible()
    page.locator("#vy-locations .vy-slot").nth(3).click()                    # picked already: a tap just inspects
    expect(page.locator("#card-inspector")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".vr__mine")).to_be_visible(timeout=15000)
    page.click(".vr__skip")
    page.locator(".vr__mine .vc--lokace").click()                            # own cards in the result open above the overlay
    expect(page.locator("#card-inspector")).to_be_visible()
    box = page.locator("#card-inspector .vc").first.bounding_box()
    assert page.evaluate("(b => document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2).closest('#card-inspector') !== null)", box)


def test_manual_plan_names_inspect_swap_and_random(page, base_url):
    _manual_table(page, base_url, bots=1)
    for _ in range(3):
        page.locator('.vy-slot[data-kind="lokace"]').first.click()
        page.locator('.vy-slot[data-kind="existence"]').first.click()
        page.click('[data-act="add"]')
    row_text = page.locator(".vm-plan__list li.is-set").first.inner_text()
    assert "(" in row_text and ")" in row_text                               # the location number stays after the name
    page.locator(".vm-plan__list").scroll_into_view_if_needed()
    page.locator(".vm-plan__loc").first.click()                              # a tap on the name inspects the card
    expect(page.locator("#card-inspector")).to_be_visible()
    page.keyboard.press("Escape")

    # Swap the locations of rows 1 and 3 by dragging one card onto the other.
    before = page.locator(".vm-plan__loc").all_inner_texts()
    a = page.locator(".vm-plan__loc").nth(0).bounding_box()
    c = page.locator(".vm-plan__loc").nth(2).bounding_box()
    page.mouse.move(a["x"] + 10, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + 20, a["y"] + a["height"] / 2 + 15, steps=3)
    page.mouse.move(c["x"] + 10, c["y"] + c["height"] / 2, steps=6)
    page.mouse.up()
    after = page.locator(".vm-plan__loc").all_inner_texts()
    assert after == [before[2], before[1], before[0]]
    ex_after = page.locator(".vm-plan__ex").all_inner_texts()
    assert len(ex_after) == 3                                                # the existences stayed where they were

    page.click("[data-shuffle]")
    expect(page.locator(".vm-plan__list li.is-set")).to_have_count(8)
    expect(page.locator('[data-act="confirm"]')).to_be_enabled()


def test_manual_cards_are_large_on_desktop_and_minus_sound_falls(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1366, "height": 800})
    ctx.add_init_script(FREQ_SPY)
    ctx.add_init_script("localStorage.setItem('baron.settings', JSON.stringify({deal:false, sound:true}))")
    pg = ctx.new_page()
    _manual_table(pg, base_url, bots=1)
    assert pg.locator('.vy-slot[data-kind="lokace"] .vc').first.bounding_box()["width"] >= 160
    _plan_all(pg)
    pg.click('[data-act="confirm"]')
    _wait_for_entry_open(pg)
    assert pg.locator(".vm-mine .vc--lokace").first.bounding_box()["width"] >= 170
    pg.evaluate("window.__freq = []")
    pg.keyboard.press("+")
    up = pg.evaluate("window.__freq.slice(0, 2)")
    pg.evaluate("window.__freq = []")
    pg.keyboard.press("-")
    down = pg.evaluate("window.__freq.slice(0, 2)")
    assert up[1] > up[0], up
    assert down[1] < down[0], down
    ctx.close()


def test_manual_base_view_has_the_decks_and_drink_counts(page, base_url):
    _manual_table(page, base_url, bots=1)
    _plan_all(page)
    page.click('[data-act="confirm"]')
    _wait_for_entry_open(page)
    page.evaluate("window.__app.socket._vydrz.submit('me', window.__app.socket._vydrz.calc.correct.me)")
    expect(page.locator(".vm-banner")).to_contain_text("výsledek", timeout=10000)
    page.click("[data-close-result]")
    for kind in ("postava", "lokace", "existence"):
        expect(page.locator(f'#vm-stage [data-pile="{kind}"]')).to_have_count(1)
    expect(page.locator("#vm-stage .vy-drinks-left li")).to_have_count(10)
    total = sum(int(t) for t in page.locator("#vm-stage .vy-drinks-left b").all_inner_texts())
    assert total == 97                                                       # three drinks were drawn in round 1
    page.click('#vm-stage [data-pile="lokace"]')
    expect(page.locator("#card-inspector")).to_be_visible()


def test_winner_splash_plays_one_party_popper_not_two_fanfares(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1000, "height": 700})
    ctx.add_init_script("""
      window.__osc = 0; window.__noise = 0;
      const AC = window.AudioContext || window.webkitAudioContext;
      const co = AC.prototype.createOscillator, cb = AC.prototype.createBufferSource;
      AC.prototype.createOscillator = function () { window.__osc++; return co.call(this); };
      AC.prototype.createBufferSource = function () { window.__noise++; return cb.call(this); };
      localStorage.setItem('baron.settings', JSON.stringify({deal: false, sound: true}));
    """)
    pg = ctx.new_page()
    pg.goto(f"{base_url}?mock=table&bots=2")
    expect(pg.locator("#vydrz-panel")).to_be_visible(timeout=8000)
    pg.evaluate("window.__osc = 0; window.__noise = 0")
    pg.evaluate("void import('/js/vydrz/winner-splash.js').then(m => { m.showBaronSplash({ name: 'Alice', isMe: true }); })")
    expect(pg.locator("#baron-splash .bs__card")).to_be_visible()
    pg.wait_for_timeout(1500)
    assert pg.evaluate("window.__noise") == 1                                # the confetti rustle, once
    assert pg.evaluate("window.__osc") <= 2                                  # bang + whistle; the old arpeggio was 8 notes
    ctx.close()
