"""Run Log: every step with its reasoning, actions, results and screenshot, so judges can replay it.

Layout of runs/<id>/:
  meta.json      task, skill, timings, outcome, handoff count
  steps.jsonl    one JSON object per step (appended as the run goes, survives crashes)
  shots/         step-NNN-*.png
  report.html    a self-updating, human-readable replay of the run
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .eyes import Observation


class RunLog:
    def __init__(self, root: str | Path, task: str, skill: str | None, secrets: list[str] | None = None):
        slug = re.sub(r"[^a-z0-9]+", "-", task.lower())[:40].strip("-") or "run"
        self.dir = Path(root) / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"
        (self.dir / "shots").mkdir(parents=True, exist_ok=True)
        self.secrets = [s for s in (secrets or []) if s and len(s) >= 3]
        self.meta: dict[str, Any] = {"task": task, "skill": skill, "started": _now(), "outcome": "running"}
        self.steps: list[dict] = []
        self._write_meta()

    def redact(self, value: Any) -> Any:
        """Remove secret values everywhere they could appear (args, results, page text)."""
        text = json.dumps(value, ensure_ascii=False)
        for s in self.secrets:
            text = text.replace(json.dumps(s, ensure_ascii=False)[1:-1], "***")
        return json.loads(text)

    def save_observation(self, obs: Observation, index: int) -> list[str]:
        paths = obs.save(self.dir / "shots", f"step-{index:03d}")
        text_file = self.dir / "shots" / f"step-{index:03d}.txt"
        text_file.write_text(self.redact(text_file.read_text(encoding="utf-8")), encoding="utf-8")
        return [str(p.relative_to(self.dir)) for p in paths]

    def step(self, record: dict) -> None:
        record = self.redact({"time": _now(), **record})
        self.steps.append(record)
        with open(self.dir / "steps.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._write_report()

    def finish(self, outcome: str, summary: str, extra: dict | None = None) -> None:
        self.meta.update({"outcome": outcome, "summary": summary, "finished": _now(), "steps": len(self.steps),
                          **(extra or {})})
        self.meta = self.redact(self.meta)
        self._write_meta()
        self._write_report()

    def _write_meta(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta, indent=2, ensure_ascii=False), encoding="utf-8")

    def _write_report(self) -> None:
        e = html.escape
        rows = []
        for s in self.steps:
            acts = "".join(
                f"<li class='{'ok' if a.get('ok') else 'bad'}'><code>{e(a['tool'])}({e(json.dumps(a.get('args', {}), ensure_ascii=False))})</code>"
                f"<pre>{e(str(a.get('result', '')))}</pre></li>" for a in s.get("actions", []))
            shots = "".join(f"<a href='{e(p)}'><img src='{e(p)}' loading='lazy'></a>" for p in s.get("shots", []))
            warn = "".join(f"<p class='warn'>⚠ {e(w)}</p>" for w in s.get("warnings", []))
            rows.append(f"""<section><h2>Step {s['index']} <small>{e(s['time'])} · {e(s.get('model', ''))} · {s.get('latency', 0):.1f}s</small></h2>
<p><b>Verify:</b> {e(s.get('verify', ''))}</p><p><b>Thought:</b> {e(s.get('thought', ''))}</p>
<p><b>Expect:</b> {e(s.get('expect', ''))}</p><ol>{acts}</ol>{warn}<div class='shots'>{shots}</div></section>""")
        m = self.meta
        page = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>Run log: {e(m['task'][:60])}</title><style>
body{{font:14px/1.45 system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px;background:#fafafa;color:#222}}
section{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:12px 0}}
h2{{font-size:16px;margin:0 0 6px}} small{{color:#777;font-weight:normal}} pre{{white-space:pre-wrap;margin:2px 0;color:#555;max-height:160px;overflow:auto}}
li.ok code{{color:#176b2c}} li.bad code{{color:#b00020}} .warn{{color:#a15c00}}
.shots img{{max-width:100%;border:1px solid #ccc;margin-top:6px}}</style></head><body>
<h1>{e(m['task'])}</h1><p>Skill: <b>{e(str(m.get('skill')))}</b> · started {e(m['started'])} · outcome: <b>{e(m['outcome'])}</b>
{(' · finished ' + e(m['finished'])) if m.get('finished') else ''} · human handoffs: {m.get('handoffs', 0)}</p>
<p>{e(m.get('summary', ''))}</p>{''.join(rows)}</body></html>"""
        (self.dir / "report.html").write_text(page, encoding="utf-8")


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")
