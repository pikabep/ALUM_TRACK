"""Observation: one look at the world, ready to send to a vision-language model."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from .screen import Shot


@dataclass
class Observation:
    text: str  # URL, numbered elements, page text, events, terminal output...
    shots: list[Shot] = field(default_factory=list)
    taken_at: float = field(default_factory=time.time)

    @property
    def fingerprint(self) -> str:
        """Changes whenever the text state changes; cheap 'did anything happen?' check."""
        return hashlib.sha1(self.text.encode()).hexdigest()[:12]

    def to_content(self) -> list[dict]:
        """Anthropic Messages API content blocks: images first, then the text."""
        blocks: list[dict] = []
        for shot in self.shots:
            blocks.append({"type": "text", "text": f"[{shot.source} screenshot, {shot.width}x{shot.height}px]"})
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": shot.b64()}})
        blocks.append({"type": "text", "text": self.text})
        return blocks

    def save(self, directory: str | Path, stem: str) -> list[Path]:
        """Write screenshots and text for the run log; returns the screenshot paths."""
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}.txt").write_text(self.text, encoding="utf-8")
        return [s.save(d / f"{stem}-{s.source}-{i}.png") for i, s in enumerate(self.shots)]
