"""Skills: markdown files that teach the generic harness a specific job.

skills/<name>/SKILL.md starts with YAML-ish front matter:

    ---
    name: irctc_tatkal
    description: Book the cheapest Tatkal ticket on IRCTC
    approve_before: ["Pay & Book", "Make Payment"]   # harness forces human approval before clicking these
    secrets: [IRCTC_USERNAME, IRCTC_PASSWORD]         # typed via {{secret:NAME}}, never shown to the model
    max_steps: 80
    ---
    ...instructions...

After a run, lessons the agent recorded with `note` are appended under "## Learned from runs".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LEARNED_HEADING = "## Learned from runs"


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    meta: dict = field(default_factory=dict)

    @property
    def approve_before(self) -> list[str]:
        return list(self.meta.get("approve_before", []))

    @property
    def secrets(self) -> list[str]:
        return list(self.meta.get("secrets", []))

    @classmethod
    def load(cls, path: Path) -> "Skill":
        text = path.read_text(encoding="utf-8")
        meta: dict = {}
        body = text
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
        if m:
            body = m.group(2)
            for line in m.group(1).splitlines():
                line = line.split(" #", 1)[0].rstrip()
                if ":" not in line:
                    continue
                key, value = (s.strip() for s in line.split(":", 1))
                try:
                    meta[key] = json.loads(value)  # lists, numbers, booleans, quoted strings
                except json.JSONDecodeError:
                    meta[key] = value
        return cls(meta.get("name", path.parent.name), meta.get("description", ""), body.strip(), path, meta)

    def learn(self, lessons: list[str], run_id: str) -> None:
        """Self-improvement: append this run's lessons to the skill file."""
        lessons = [l.strip() for l in lessons if l.strip()]
        if not lessons:
            return
        text = self.path.read_text(encoding="utf-8").rstrip() + "\n"
        if LEARNED_HEADING not in text:
            text += f"\n{LEARNED_HEADING}\n"
        text += f"\n### {datetime.now():%Y-%m-%d %H:%M} (run {run_id})\n" + "".join(f"- {l}\n" for l in lessons)
        self.path.write_text(text, encoding="utf-8")


class SkillLibrary:
    def __init__(self, root: str | Path = "skills"):
        self.root = Path(root)

    def all(self) -> list[Skill]:
        return [Skill.load(p) for p in sorted(self.root.glob("*/SKILL.md"))]

    def get(self, name: str) -> Skill:
        for s in self.all():
            if s.name == name or s.path.parent.name == name:
                return s
        raise KeyError(f"no skill named {name!r} in {self.root}")

    def match(self, task: str) -> Skill | None:
        """Pick the skill whose name/description shares the most words with the task."""
        words = set(re.findall(r"[a-z0-9]{3,}", task.lower()))
        best, score = None, 0
        for s in self.all():
            overlap = len(words & set(re.findall(r"[a-z0-9]{3,}", f"{s.name} {s.description}".lower().replace("_", " "))))
            if overlap > score:
                best, score = s, overlap
        return best if score >= 2 else None

    def index(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self.all()) or "(no skills)"
