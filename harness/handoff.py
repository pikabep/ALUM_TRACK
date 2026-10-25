"""Human Handoff: pause, ask a person through the agent's own channel, resume.

Channels are polled side by side; the first answer from any of them wins. The
terminal works when someone is at the keyboard; Telegram reaches your phone,
which is what an unattended 11:00 run needs.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

YES = {"y", "yes", "ok", "okay", "approve", "approved", "approve payment", "go", "pay", "haan", "ha", "👍"}


class Channel(Protocol):
    name: str

    def send(self, text: str, image: bytes | None = None, urgent: bool = False) -> None: ...
    def start_listening(self) -> None: ...
    def poll(self) -> str | None: ...


class TerminalChannel:
    name = "terminal"

    def __init__(self, notify: bool = True):
        self.notify = notify and shutil.which("notify-send") is not None
        self._lines: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None

    @property
    def usable(self) -> bool:
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except ValueError:  # stdin closed
            return False

    def send(self, text: str, image: bytes | None = None, urgent: bool = False) -> None:
        if not urgent:
            print(f"[agent] {text}", flush=True)
            return
        print(f"\n\a🙋 AGENT NEEDS YOU\n{text}\n> ", end="", flush=True)
        if self.notify:
            subprocess.run(["notify-send", "-u", "critical", "Agent needs you", text[:200]], check=False,
                           capture_output=True)

    def start_listening(self) -> None:
        # select() on stdin does not work on Windows, so a daemon thread reads lines instead.
        if self.usable and self._reader is None:
            def read() -> None:
                for line in sys.stdin:
                    self._lines.put(line.strip())
            self._reader = threading.Thread(target=read, daemon=True)
            self._reader.start()
        while not self._lines.empty():  # drop anything typed before the question
            self._lines.get_nowait()

    def poll(self) -> str | None:
        try:
            return self._lines.get_nowait() or None
        except queue.Empty:
            return None


class TelegramChannel:
    """Talks to one chat through a bot you create with @BotFather (free)."""

    name = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.http = httpx.Client(timeout=40)
        self.offset: int | None = None

    @classmethod
    def from_env(cls) -> "TelegramChannel | None":
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        return cls(token, chat) if token and chat else None

    def send(self, text: str, image: bytes | None = None, urgent: bool = False) -> None:
        for attempt in range(3):
            try:
                if image:
                    self.http.post(f"{self.api}/sendPhoto", data={"chat_id": self.chat_id, "caption": text[:1000]},
                                   files={"photo": ("screen.png", image, "image/png")})
                    if len(text) > 1000:
                        self.http.post(f"{self.api}/sendMessage", data={"chat_id": self.chat_id, "text": text[1000:4000]})
                else:
                    self.http.post(f"{self.api}/sendMessage", data={"chat_id": self.chat_id, "text": text[:4000]})
                return
            except httpx.HTTPError:
                time.sleep(2 ** attempt)

    def start_listening(self) -> None:
        # Skip anything sent before the question, so a stale "yes" can never approve a payment.
        updates = self._updates(timeout=0)
        if updates:
            self.offset = updates[-1]["update_id"] + 1

    def poll(self) -> str | None:
        for u in self._updates(timeout=2):
            self.offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) == self.chat_id and msg.get("text"):
                return msg["text"].strip()
        return None

    def _updates(self, timeout: int) -> list[dict]:
        params = {"timeout": timeout, **({"offset": self.offset} if self.offset is not None else {})}
        try:
            return self.http.get(f"{self.api}/getUpdates", params=params).json().get("result", [])
        except (httpx.HTTPError, ValueError):
            time.sleep(1)
            return []


@dataclass
class HandoffRecord:
    kind: str  # "question" | "approval"
    prompt: str
    answer: str | None
    asked_at: float
    answered_at: float | None
    channel: str | None


@dataclass
class Handoff:
    channels: list = field(default_factory=list)
    records: list[HandoffRecord] = field(default_factory=list)

    @classmethod
    def default(cls) -> "Handoff":
        channels: list = [TerminalChannel()]
        tg = TelegramChannel.from_env()
        if tg:
            channels.append(tg)
        return cls(channels)

    def notify(self, text: str, image: bytes | None = None, urgent: bool = False) -> None:
        """One-way update (e.g. 'booked, PNR 123'). Not an intervention."""
        for ch in self.channels:
            try:
                ch.send(text, image, urgent)
            except Exception:
                pass

    def ask(self, question: str, image: bytes | None = None, timeout: float = 600, kind: str = "question") -> str | None:
        rec = HandoffRecord(kind, question, None, time.time(), None, None)
        self.records.append(rec)
        if not any(getattr(ch, "usable", True) for ch in self.channels):
            # nobody can answer (no terminal attached, no Telegram): don't block the run for nothing
            self.notify(f"[unanswerable, no reply channel] {question}")
            return None
        for ch in self.channels:
            ch.start_listening()
        self.notify(question, image, urgent=True)
        deadline = time.monotonic() + timeout
        next_ping = time.monotonic() + 60
        while time.monotonic() < deadline:
            for ch in self.channels:
                answer = ch.poll()
                if answer:
                    rec.answer, rec.answered_at, rec.channel = answer, time.time(), ch.name
                    for other in self.channels:
                        if other is not ch and hasattr(other, "answered"):
                            other.answered(answer, ch.name)  # the other card says "answered elsewhere"
                    return answer
            if time.monotonic() > next_ping:
                self.notify(f"(reminder) still waiting for you: {question.splitlines()[0][:120]}")
                next_ping = time.monotonic() + 60
            time.sleep(0.2)
        return None

    def approve(self, summary: str, image: bytes | None = None, timeout: float = 900) -> bool:
        answer = self.ask(f"APPROVAL NEEDED\n{summary}\n\nReply YES to approve, anything else to refuse.",
                          image, timeout, kind="approval")
        approved = bool(answer) and answer.strip().lower().strip("!. ") in YES
        self.notify("Approved, continuing." if approved else "Not approved, stopping that action.")
        return approved
