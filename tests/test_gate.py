"""The payment gate is enforced in code: every route to a pay click needs a human yes.

Also covers the success checks on done (done_requires) and the one-shot lock (once: true).
"""
import json
from pathlib import Path

import pytest

from harness.hands import BrowserHand
from tests.test_loop import Script, make_agent

PAGE = """<!doctype html><html><body>
<label><input type="radio" id="upi" name="m"> Pay through BHIM/UPI</label>
<button id="pay" style="position:absolute;left:100px;top:200px;width:160px;height:50px"
  onclick="document.getElementById('out').textContent='PAID'">Pay Now ₹45</button>
<p id="out"></p></body></html>"""


@pytest.fixture
def page_url(tmp_path_factory):
    # not under tmp_path: its folder is named after the test, and "pay" in a URL trips the gate by design
    p = tmp_path_factory.mktemp("site") / "shop.html"
    p.write_text(PAGE, encoding="utf-8")
    return p.resolve().as_uri()


@pytest.fixture
def browser(tmp_path):
    b = BrowserHand(profile_dir=tmp_path / "profile", headless=True, downloads_dir=tmp_path / "dl")
    yield b
    b.close()


def paid(browser) -> bool:
    return browser.extract(selector="#out").output == "PAID"


@pytest.mark.parametrize("action", [
    {"tool": "browser_click", "args": {"selector": "#pay"}},
    {"tool": "browser_click", "args": {"text": "Pay Now"}},
    {"tool": "browser_click", "args": {"x": 180, "y": 225}},                       # coordinates
    {"tool": "browser_eval", "args": {"script": "document.getElementById('pay').click()"}},
])
def test_every_route_to_pay_is_blocked_without_approval(tmp_path, browser, page_url, action):
    script = Script([{"actions": [{"tool": "browser_goto", "args": {"url": page_url}}]},
                     {"actions": [action]},
                     {"actions": [{"tool": "done", "args": {"summary": "x", "success": False}}]}])
    agent, channel = make_agent(tmp_path, browser, script, answers=["no"])
    agent.run("buy it")
    assert not paid(browser)
    assert any("APPROVAL NEEDED" in s for s in channel.sent)
    assert any("Pay Now" in s and "45" in s for s in channel.sent)  # summary comes from the live page


def test_enter_on_focused_pay_button_is_blocked(tmp_path, browser, page_url):
    script = Script([{"actions": [{"tool": "browser_goto", "args": {"url": page_url}},
                                  {"tool": "browser_eval", "args": {"script": "document.getElementById('pay').focus()"}}]},
                     {"actions": [{"tool": "browser_press", "args": {"key": "Enter"}}]},
                     {"actions": [{"tool": "done", "args": {"summary": "x", "success": False}}]}])
    agent, _ = make_agent(tmp_path, browser, script, answers=["no"])
    agent.run("buy it")
    assert not paid(browser)


def test_approval_allows_the_pay_click(tmp_path, browser, page_url):
    script = Script([{"actions": [{"tool": "browser_goto", "args": {"url": page_url}}]},
                     {"actions": [{"tool": "browser_click", "args": {"selector": "#pay"}}]},
                     {"actions": [{"tool": "done", "args": {"summary": "x"}}]}])
    agent, _ = make_agent(tmp_path, browser, script, answers=["Approve payment"])
    agent.run("buy it")
    assert paid(browser)


def test_payment_mode_radio_is_not_a_payment(tmp_path, browser, page_url):
    script = Script([{"actions": [{"tool": "browser_goto", "args": {"url": page_url}},
                                  {"tool": "browser_click", "args": {"selector": "#upi"}}]},
                     {"actions": [{"tool": "done", "args": {"summary": "x"}}]}])
    agent, channel = make_agent(tmp_path, browser, script)
    agent.run("choose upi")
    assert not channel.sent or not any("APPROVAL" in s for s in channel.sent)


def _once_skill(tmp_path):
    d = tmp_path / "skills" / "book"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text('---\nname: book\ndescription: book demo\nonce: true\n'
                                'done_requires: ["\\\\bPNR\\\\b[^\\\\d]{0,40}\\\\d{10}"]\n---\nBook.\n', encoding="utf-8")


def test_done_needs_visible_proof_and_lock_prevents_second_booking(tmp_path, browser, monkeypatch):
    _once_skill(tmp_path)
    monkeypatch.chdir(tmp_path)
    no_pnr = (tmp_path / "a.html")
    no_pnr.write_text("<p>Booking pending</p>", encoding="utf-8")
    pnr = (tmp_path / "b.html")
    pnr.write_text("<p>Booked. PNR No: 2412345678</p>", encoding="utf-8")
    script = Script([{"actions": [{"tool": "browser_goto", "args": {"url": no_pnr.as_uri()}}]},
                     {"actions": [{"tool": "done", "args": {"summary": "booked"}}]},           # refused
                     {"actions": [{"tool": "browser_goto", "args": {"url": pnr.as_uri()}}]},
                     {"actions": [{"tool": "done", "args": {"summary": "booked"}}]}])
    agent, _ = make_agent(tmp_path, browser, script)
    result = agent.run("book", skill="book")
    assert result.success and result.steps == 4
    assert "done refused" in json.dumps(script.requests[2])
    assert list((tmp_path / "state").glob("book.done.lock"))
    again = agent.run("book", skill="book")
    assert again.success and again.summary.startswith("skipped")
