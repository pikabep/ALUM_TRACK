"""Triggers: start a run at a time, when something happens, or from a chat message."""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .handoff import TelegramChannel


def parse_when(when: str) -> datetime:
    """'10:59:30' means the next time the clock shows that; ISO datetimes are taken as-is."""
    try:
        return datetime.fromisoformat(when)
    except ValueError:
        pass
    parts = [int(p) for p in when.split(":")]
    now = datetime.now()
    target = now.replace(hour=parts[0], minute=parts[1], second=parts[2] if len(parts) > 2 else 0, microsecond=0)
    if target > now:
        return target
    # Started a little late (e.g. at 10:55 for 10:52): run now rather than tomorrow and miss the window.
    return now if now - target < timedelta(hours=2) else target + timedelta(days=1)


def wait_until(target: datetime, say: Callable[[str], None] = print) -> None:
    say(f"Scheduled for {target:%Y-%m-%d %H:%M:%S}; waiting {target - datetime.now()}")
    last_report = 0.0
    while (remaining := (target - datetime.now()).total_seconds()) > 0:
        if time.monotonic() - last_report > 600:
            say(f"  {timedelta(seconds=int(remaining))} to go")
            last_report = time.monotonic()
        # coarse sleeps first, then fine ones, so we wake within ~10 ms of the target
        time.sleep(min(remaining - 0.5, 30) if remaining > 1 else 0.01)


def wait_for_file(path: str, interval: float = 2) -> None:
    while not Path(path).expanduser().exists():
        time.sleep(interval)


def wait_for_command(command: str, interval: float = 30) -> None:
    """Event trigger: runs `command` until it exits 0 (e.g. a script that checks for an email)."""
    while subprocess.run(command, shell=True, capture_output=True).returncode != 0:
        time.sleep(interval)


def serve_telegram(run: Callable[[str, str | None], str]) -> None:
    """Chat trigger. Message the bot: `/run <task>` or `/run <skill>: <task>`."""
    tg = TelegramChannel.from_env()
    if tg is None:
        raise SystemExit("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first")
    tg.start_listening()
    tg.send("Agent online. Send /run <task> or /run <skill>: <task>")
    while True:
        text = tg.poll()
        if not text or not text.startswith("/run"):
            continue
        body = text[4:].strip()
        skill = None
        if ":" in body.split(" ", 1)[0]:
            skill, body = (s.strip() for s in body.split(":", 1))
        tg.send(f"Starting: {body}" + (f" (skill {skill})" if skill else ""))
        tg.send(run(body, skill))
        tg.start_listening()
