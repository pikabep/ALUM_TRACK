"""Configuration: config.toml for settings, .env for keys and secrets (never committed)."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from .eyes import Eyes
from .handoff import Handoff
from .hands import BrowserHand, DesktopHand, FileHand, Hands, ShellHand
from .llm import ModelClient, Provider
from .skills import SkillLibrary


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_config(path: str | Path = "config.toml") -> dict[str, Any]:
    load_dotenv(Path(path).with_name(".env"))
    p = Path(path)
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def build_model(cfg: dict) -> ModelClient:
    models = cfg.get("models", {})
    chain = models.get("chain", ["gemini", "groq", "openrouter", "ollama"])
    return ModelClient([Provider.from_config(name, models.get(name)) for name in chain])


def build_agent(cfg: dict, bus=None, inbox=None, channels: list | None = None):
    from .loop import Agent

    b, a = cfg.get("browser", {}), cfg.get("agent", {})
    server = os.environ.get("HARNESS_HEADLESS") == "1"
    handoff = Handoff.default()
    handoff.channels = list(channels or []) + handoff.channels  # the app first; terminal/Telegram mirror it
    hands = Hands(
        shell=ShellHand(confirm=lambda q: handoff.approve(q)),
        files=FileHand(),
        browser=BrowserHand(profile_dir=os.environ.get("HARNESS_PROFILE") or b.get("profile_dir", "~/.harness/browser-profile"),
                            # HARNESS_HEADLESS=1 (servers such as Render): bundled Chromium, no window
                            channel=None if server else (b.get("channel") or None),
                            cdp_url=b.get("cdp_url") or None,
                            headless=server or b.get("headless", False),
                            downloads_dir=Path(a.get("runs_dir", "runs")) / "downloads"),
        desktop=DesktopHand(),
    )
    return Agent(hands, Eyes(), build_model(cfg), handoff, SkillLibrary(a.get("skills_dir", "skills")),
                 runs_dir=a.get("runs_dir", "runs"), max_steps=a.get("max_steps", 60), bus=bus, inbox=inbox,
                 safety=cfg.get("safety", {}), state_dir=a.get("state_dir", "state"))
