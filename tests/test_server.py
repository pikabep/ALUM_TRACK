"""Companion app server: token, event replay, approvals answered from the app, OTP inbox."""
import json
import threading
import time

import httpx

from harness.bus import EventBus
from harness.handoff import Handoff
from harness.inbox import Inbox
from harness.server import App, WebChannel, serve


def test_app_approval_roundtrip_and_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "t0k")
    monkeypatch.setenv("INBOX_SECRET", "s3")
    bus, inbox = EventBus(), Inbox()
    channel = WebChannel(bus, tmp_path)
    app = App(bus, channel, inbox, make_agent=lambda: None)
    serve(app, tmp_path, port=7791, open_browser=False)
    base = "http://127.0.0.1:7791"

    assert httpx.post(f"{base}/api/command", json={"token": "wrong", "cmd": "send", "text": "x"}).status_code == 403
    assert "Skilled Hands" in httpx.get(base + "/").text

    result = {}
    t = threading.Thread(target=lambda: result.setdefault("ok", Handoff([channel]).approve("Pay ₹45?", b"\x89PNG", 10)))
    t.start()
    for _ in range(50):
        if channel.pending:
            break
        time.sleep(0.1)
    assert httpx.post(f"{base}/api/command", json={"token": "t0k", "cmd": "send", "text": "Approve payment"}).json()["ok"]
    t.join(5)
    assert result["ok"] is True

    kinds = [e["type"] for e in bus._history]
    ask = next(e for e in bus._history if e["type"] == "ask")
    assert ask["kind"] == "approval" and ask["image"].endswith(".png") and "answered" in kinds
    assert httpx.get(base + ask["image"]).status_code == 200
    assert httpx.get(f"{base}/files/../../etc/passwd").status_code == 404

    # history is replayed to a client that connects late
    with httpx.stream("GET", f"{base}/events?token=t0k", timeout=5) as r:
        first = next(line for line in r.iter_lines() if line.startswith("data:"))
    assert json.loads(first[5:])["type"] == "say"

    # the phone's SMS forwarder posts an OTP; only codes after the wait started count
    threading.Timer(0.5, lambda: httpx.post("http://127.0.0.1:7792/inbox",
                                            json={"secret": "s3", "from": "AX-IRCTCI", "text": "OTP is 482913"})).start()
    res = inbox.wait_for_code(timeout=5, sender="IRCTC")
    assert res.ok and "482913" in res.output
    assert httpx.post("http://127.0.0.1:7792/inbox", json={"secret": "bad", "text": "1234"}).status_code == 403
