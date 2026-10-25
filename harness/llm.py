"""Model layer: any OpenAI-compatible chat API, with a fallback chain of providers.

Free options that speak this API: Google Gemini (AI Studio key), Groq, OpenRouter
(":free" models) and a local Ollama. When one rate-limits or errors, the next one
takes the step, so a run survives a quota running out mid-booking. Each request is
self-contained (the loop rebuilds the prompt every step), so switching provider
mid-run loses nothing.
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from PIL import Image

PRESETS: dict[str, dict[str, Any]] = {
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY",
               "model": "gemini-3.5-flash", "extra": {"reasoning_effort": "low"}},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
             "model": "meta-llama/llama-4-scout-17b-16e-instruct"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
                   "model": "google/gemma-4-31b-it:free"},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None, "model": "qwen2.5vl:7b"},
}


class ModelUnavailable(RuntimeError):
    """Every provider in the chain failed or is cooling down."""


@dataclass
class Provider:
    name: str
    base_url: str
    model: str
    key_env: str | None = None
    json_mode: bool = True
    extra: dict[str, Any] = field(default_factory=dict)
    cooldown_until: float = 0.0  # set after a long rate-limit, so we skip it for a while

    @classmethod
    def from_config(cls, name: str, overrides: dict[str, Any] | None = None) -> "Provider":
        overrides = overrides or {}
        cfg = {**PRESETS.get(overrides.get("preset", name), {}), **overrides}  # preset= lets one API appear twice
        return cls(name=name, base_url=cfg["base_url"].rstrip("/"), model=cfg["model"],
                   key_env=cfg.get("key_env"), json_mode=cfg.get("json_mode", True), extra=cfg.get("extra", {}))

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.key_env) if self.key_env else None

    @property
    def configured(self) -> bool:
        return self.key_env is None or bool(self.api_key)


@dataclass
class Reply:
    text: str
    provider: str
    model: str
    latency: float


class ModelClient:
    def __init__(self, providers: list[Provider], timeout: float = 45, retries: int = 2,
                 max_wait: float = 20, transport: httpx.BaseTransport | None = None):
        self.providers = providers
        self.retries = retries  # per provider, for short waits; longer waits move to the next provider
        self.max_wait = max_wait
        self.http = httpx.Client(timeout=timeout, transport=transport)
        self.events: list[str] = []

    def available(self) -> list[Provider]:
        now = time.time()
        return [p for p in self.providers if p.configured and p.cooldown_until <= now]

    def complete(self, messages: list[dict], json_mode: bool = True, max_tokens: int = 3000) -> Reply:
        errors = []
        for p in self.available():
            use_json = json_mode and p.json_mode
            for attempt in range(self.retries + 1):
                payload = {"model": p.model, "messages": messages, "temperature": 0.2,
                           "max_tokens": max_tokens, **p.extra}
                if use_json:
                    payload["response_format"] = {"type": "json_object"}
                headers = {"Authorization": f"Bearer {p.api_key}"} if p.api_key else {}
                t0 = time.monotonic()
                try:
                    r = self.http.post(f"{p.base_url}/chat/completions", json=payload, headers=headers)
                except httpx.TimeoutException:
                    # an overloaded model rarely recovers within seconds: hand the step to the next one
                    errors.append(f"{p.name}: timed out")
                    p.cooldown_until = time.time() + 120
                    self.events.append(f"{p.name} ({p.model}) timed out; using the next model for 2 min")
                    break
                except httpx.HTTPError as e:
                    errors.append(f"{p.name}: {type(e).__name__}")
                    self._sleep(attempt, f"{p.name} network error {type(e).__name__}")
                    continue
                if r.status_code == 200:
                    text = _content(r.json())
                    if text.strip():
                        return Reply(text, p.name, p.model, time.monotonic() - t0)
                    errors.append(f"{p.name}: empty reply")
                    continue
                if r.status_code == 400 and use_json and "response_format" in r.text:
                    use_json = False  # this model has no JSON mode; the prompt still asks for JSON
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = _retry_after(r) or 2 ** attempt * 2
                    errors.append(f"{p.name}: HTTP {r.status_code}")
                    if wait > self.max_wait or attempt == self.retries:
                        p.cooldown_until = time.time() + max(wait, 30)
                        self.events.append(f"{p.name} rate-limited/unavailable; cooling down {max(wait, 30):.0f}s")
                        break
                    self._sleep(attempt, f"{p.name} HTTP {r.status_code}", wait)
                    continue
                errors.append(f"{p.name}: HTTP {r.status_code} {r.text[:200]}")
                break  # auth / bad request: this provider will not recover by retrying
        raise ModelUnavailable("; ".join(errors) or "no model provider is configured (set an API key)")

    def _sleep(self, attempt: int, why: str, wait: float | None = None) -> None:
        wait = wait if wait is not None else 2 ** attempt + random.random()
        self.events.append(f"{why}; retrying in {wait:.1f}s")
        time.sleep(wait)


def _content(data: dict) -> str:
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, list):  # some providers return content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content


def _retry_after(r: httpx.Response) -> float | None:
    header = r.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"|try again in (\d+(?:\.\d+)?)s', r.text)
    return float(m.group(1) or m.group(2)) if m else None


def parse_json(text: str) -> dict:
    """Pull the JSON object out of a reply, tolerating code fences and <think> blocks."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    obj = json.loads(candidate)
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    return obj


def to_openai_content(blocks: list[dict], jpeg_quality: int = 80) -> list[dict]:
    """Convert Anthropic-style blocks (Observation.to_content) to OpenAI parts.
    Screenshots are re-encoded as JPEG: a fraction of the bytes, which matters on a hotspot."""
    out = []
    for b in blocks:
        if b["type"] == "text":
            out.append({"type": "text", "text": b["text"]})
        elif b["type"] == "image":
            img = Image.open(io.BytesIO(base64.b64decode(b["source"]["data"]))).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=jpeg_quality)
            url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            out.append({"type": "image_url", "image_url": {"url": url}})
    return out
