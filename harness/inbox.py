"""Inbox: messages that arrive from outside the computer, e.g. an OTP SMS forwarded by the phone.

An SMS-forwarder app on the phone POSTs each matching SMS to the companion server
(/inbox, guarded by INBOX_SECRET). The agent reads codes with inbox_wait_for_code,
which only returns messages that arrived after it started waiting, so a stale OTP
is never typed. Codes are redacted from the run log like any other secret.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from .hands.base import ActionResult, Tool, schema


@dataclass
class Message:
    received: float
    sender: str
    text: str


class Inbox:
    def __init__(self):
        self.messages: list[Message] = []
        self._cond = threading.Condition()
        self.secrets: list[str] = []  # codes handed to the agent; the run log redacts them

    def add(self, text: str, sender: str = "") -> None:
        with self._cond:
            self.messages.append(Message(time.time(), sender, text))
            self._cond.notify_all()

    def wait_for_code(self, timeout: float = 60, sender: str = "", digits: str = "4,8") -> ActionResult:
        lo, hi = (int(d) for d in digits.split(","))
        pattern = re.compile(rf"(?<!\d)(\d{{{lo},{hi}}})(?!\d)")
        since = time.time() - 2  # tolerate a code that landed just before the call
        deadline = time.monotonic() + min(float(timeout), 300)
        with self._cond:
            while True:
                for m in reversed(self.messages):
                    if m.received < since or (sender and not re.search(sender, m.sender + " " + m.text, re.I)):
                        continue
                    found = pattern.search(m.text)
                    if found:
                        code = found.group(1)
                        self.secrets.append(code)
                        return ActionResult(True, f"code {code} (from {m.sender or 'phone'}); type it now, it is single-use")
                left = deadline - time.monotonic()
                if left <= 0:
                    return ActionResult.fail(f"no code arrived within {timeout:g}s. Click 'Resend OTP' once and wait "
                                             "again; if it still does not come, ask_human for it.")
                self._cond.wait(min(left, 1.0))

    def tools(self) -> list[Tool]:
        return [Tool("inbox_wait_for_code",
                     "Wait for a one-time code (OTP) forwarded from the phone and return it. Only codes that arrive "
                     "after this call starts count. sender: optional regex, e.g. 'IRCTC|UIDAI'.",
                     schema((), timeout={"type": "number", "description": "seconds, default 60"},
                            sender={"type": "string"}), self.wait_for_code)]
