"""Tests for the Hands and Eyes. Run the desktop tests under a display:
    xvfb-run -s "-screen 0 1920x1080x24" .venv/bin/pytest -q
"""
import os
import re
import time
from pathlib import Path

import pytest

from harness.eyes import Eyes, diff_ratio, fit
from harness.eyes.screen import Geometry
from harness.hands import BrowserHand, DesktopHand, FileHand, Hands, ShellHand, retry

FIXTURE = (Path(__file__).parent / "fixtures" / "form.html").resolve().as_uri()


# ---------------- base ----------------

def test_retry_backoff_then_success():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("boom")
        return "ok"

    assert retry(flaky, attempts=3, base_delay=0.01) == "ok"
    assert len(calls) == 3


def test_retry_if_result_and_give_up():
    calls = []
    out = retry(lambda: calls.append(1) or 503, attempts=2, base_delay=0.01, retry_if=lambda r: r == 503)
    assert out == 503 and len(calls) == 2
    with pytest.raises(ValueError):
        retry(lambda: (_ for _ in ()).throw(ValueError("x")), attempts=2, base_delay=0.01)


def test_tool_wraps_bad_arguments():
    tools = Hands().tools()
    res = tools["file_read"](nonsense=1)
    assert not res.ok and "TypeError" in res.error
    assert len(tools) == len({t for t in tools})  # no duplicate names


# ---------------- shell ----------------

def test_shell_basic_and_cwd_persistence(tmp_path):
    sh = ShellHand(cwd=tmp_path)
    assert sh.run("echo hello").output.strip() == "hello"
    (tmp_path / "sub").mkdir()
    sh.run("cd sub")
    assert sh.cwd == tmp_path / "sub"
    assert sh.run("pwd").output.strip() == str(tmp_path / "sub")


def test_shell_failure_and_timeout(tmp_path):
    sh = ShellHand(cwd=tmp_path)
    res = sh.run("echo partial; exit 3")
    assert not res.ok and res.data["exit_code"] == 3 and "partial" in res.output
    t0 = time.monotonic()
    res = sh.run("echo started; sleep 30", timeout=1)
    assert not res.ok and "timed out" in res.error and "started" in res.output
    assert time.monotonic() - t0 < 5


def test_shell_guard_and_background(tmp_path):
    assert not ShellHand(cwd=tmp_path).run("rm -rf / ").ok
    asked = []
    sh = ShellHand(cwd=tmp_path, confirm=lambda q: asked.append(q) or False)
    assert not sh.run("sudo reboot").ok and asked
    res = sh.run("echo bg-out; sleep 0.2", background=True)
    assert res.ok
    time.sleep(0.6)
    assert "bg-out" in Path(res.data["log"]).read_text()


# ---------------- files ----------------

def test_files_roundtrip(tmp_path):
    fh = FileHand(root=tmp_path)
    assert fh.write("a/b.txt", "one\ntwo\nthree\n").ok
    assert fh.read("a/b.txt", start_line=2, end_line=2).output == "two\n"
    assert fh.append("a/b.txt", "four\n").ok
    assert fh.edit("a/b.txt", "two", "TWO").ok
    assert "TWO" in fh.read("a/b.txt").output
    fh.write("dup.txt", "x x")
    assert not fh.edit("dup.txt", "x", "y").ok
    assert fh.edit("dup.txt", "x", "y", replace_all=True).ok
    assert "b.txt" in fh.list("a").output
    assert not fh.read("missing.txt").ok


# ---------------- geometry ----------------

def test_geometry_roundtrip():
    assert fit(1920, 1080) == (1366, 768)
    assert fit(1280, 800) == (1280, 800)  # never upscale
    g = Geometry.for_size(2880, 1800, left=100, top=0)
    x, y = g.to_input(683, 427)
    assert g.to_image(x, y) == (683, 427)


# ---------------- browser ----------------

@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    b = BrowserHand(profile_dir=tmp_path_factory.mktemp("profile"), headless=not os.environ.get("DISPLAY"),
                    downloads_dir=tmp_path_factory.mktemp("dl"), nav_retries=2)
    yield b
    b.close()


def ref_of(snap, pattern):
    for line in snap.lines:
        if re.search(pattern, line):
            return int(line.split("]")[0][1:])
    raise AssertionError(f"{pattern!r} not in:\n" + "\n".join(snap.lines))


def test_browser_snapshot_lists_elements(browser):
    assert browser.goto(FIXTURE).ok
    snap, shot = Eyes().browser(browser)
    text = "\n".join(snap.lines)
    assert re.search(r'input\[text\] "From station \| From\*"', text)
    assert "select" in text and "TATKAL" in text
    assert 'div(clickable) "AC Chair Car (CC)"' in text          # cursor:pointer div, not its span
    assert text.count("AC Chair Car") == 1
    assert "Covered" not in text                                   # hidden behind the overlay
    assert "Far below" not in text and snap.offscreen >= 1          # outside the viewport
    assert "Pay inside frame" in text                               # iframe content
    assert shot is not None and (shot.width, shot.height) == fit(1280, 800)


def test_browser_type_select_click_wait(browser):
    browser.goto(FIXTURE)
    snap, _ = Eyes().browser(browser, with_screenshot=False)
    assert browser.type("ROORKEE", ref=ref_of(snap, r"From station")).ok
    assert browser.type("hunter2", ref=ref_of(snap, r"input\[password\]")).ok
    assert browser.select(ref=ref_of(snap, r"^\[\d+\] select"), label="TATKAL").ok
    assert browser.click(ref=ref_of(snap, r'"Search"')).ok
    res = browser.wait(text="Found 3 trains", timeout=5)
    assert res.ok, res.error
    assert "ROORKEE / TQ" in browser.extract(selector="#result").output
    snap2, _ = Eyes().browser(browser, with_screenshot=False)
    assert "hunter2" not in snap2.to_text() and "value=<hidden>" in snap2.to_text()


def test_browser_clickable_div_iframe_and_dialog(browser):
    browser.goto(FIXTURE)
    snap, _ = Eyes().browser(browser, with_screenshot=False)
    assert browser.click(ref=ref_of(snap, "AC Chair Car")).ok
    assert browser.extract(selector="#result").output == "class CC chosen"
    assert browser.click(ref=ref_of(snap, "Pay inside frame")).ok
    assert browser.page.frames[1].locator("#inner").inner_text() == "inner clicked"
    assert browser.click(ref=ref_of(snap, "Alert me")).ok
    assert any("Session expired" in e for e in browser.drain_events())


def test_browser_covered_element_needs_force(browser):
    browser.goto(FIXTURE)
    # a full-screen "Please wait" loader, like IRCTC's, that scrolling cannot get around
    browser.page.evaluate("""() => { const d = document.createElement('div');
        d.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:9';
        document.body.appendChild(d); }""")
    snap, _ = Eyes().browser(browser, with_screenshot=False)
    assert not any("Search" in line for line in snap.lines)          # covered elements are not offered
    res = browser.click(selector="#hidden-behind", timeout=1.5)
    assert not res.ok and "intercepts pointer events" in res.error   # model sees why
    assert browser.click(selector="#hidden-behind", force=True).ok
    assert browser.page.title() == "covered clicked"


def test_browser_click_by_coordinates_and_stale_ref(browser):
    browser.goto(FIXTURE)
    snap, _ = Eyes().browser(browser, with_screenshot=False)
    ref = ref_of(snap, "AC Chair Car")
    box = next(b for b in snap.boxes if b["id"] == ref)
    g = browser.geometry()
    x, y = g.to_image(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    assert browser.click(x=x, y=y).ok
    assert browser.extract(selector="#result").output == "class CC chosen"
    browser.reload()
    res = browser.click(ref=ref)
    assert not res.ok and "Observe again" in res.error


def test_browser_goto_failure_is_reported(browser):
    t0 = time.monotonic()
    res = browser.goto("http://127.0.0.1:9/")
    assert not res.ok and "after 2 attempts" in res.error
    assert time.monotonic() - t0 < 15


def test_browser_new_tab_becomes_active(browser):
    browser.goto(FIXTURE)
    browser.page.evaluate(f"window.open({FIXTURE!r} + '#popup')")
    browser.page.wait_for_timeout(800)  # let the "page" event arrive
    obs = Eyes().observe(browser)
    assert "#popup" in browser.page.url and "new tab opened" in obs.text and "<- active" in obs.text
    assert browser.switch_tab(0).ok and "#popup" not in browser.page.url


def test_observation_content_blocks(browser):
    browser.goto(FIXTURE)
    obs = Eyes().observe(browser, terminal="$ ls\nfoo")
    blocks = obs.to_content()
    assert [b["type"] for b in blocks] == ["text", "image", "text"]
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert "Last terminal output" in blocks[-1]["text"]


# ---------------- desktop (needs a display) ----------------

needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY") and os.name == "posix", reason="no display")


@needs_display
def test_desktop_capture_and_coordinate_mapping():
    eyes, hand = Eyes(), DesktopHand()
    a = eyes.desktop()
    g = hand.geometry()
    assert (a.width, a.height) == (g.img_w, g.img_h)
    assert hand.move(683, 384).ok
    real = hand.gui.position()
    assert (real.x, real.y) == g.to_input(683, 384)
    assert hand.cursor() == (683, 384)
    assert diff_ratio(a, a) == 0.0
    assert not hand.click(99999, 5).ok  # outside the screenshot: refused, not clamped


@needs_display
def test_desktop_sees_browser_change(browser):
    """End-to-end through the OS: a headed browser on screen, clicked with the real mouse."""
    if browser.headless:
        pytest.skip("headless browser is not on screen")
    browser.goto(FIXTURE)
    eyes, hand = Eyes(), DesktopHand()
    before = eyes.desktop()
    browser.page.evaluate("document.body.style.background = 'black'")
    browser.page.wait_for_timeout(300)
    after = eyes.desktop()
    assert diff_ratio(before, after) > 0.2
