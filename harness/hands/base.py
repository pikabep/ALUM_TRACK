"""Shared building blocks for every Hand: a uniform result type, tool specs, and retry."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass
class ActionResult:
    """What every action returns. Hands never raise into the loop: failures become
    data the model can read and recover from."""

    ok: bool
    output: str = ""
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fail(cls, error: str, output: str = "", **data: Any) -> "ActionResult":
        return cls(False, output, error, data)

    def to_text(self, limit: int = 4000) -> str:
        if self.ok:
            return truncate(self.output or "ok", limit)
        text = f"ERROR: {self.error}" + (f"\n{self.output}" if self.output else "")
        return truncate(text, limit)


def truncate(text: str, limit: int) -> str:
    """Keep the head and tail of long text; the end of a log is usually what matters."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n... [{len(text) - limit} chars truncated] ...\n{text[-tail:]}"


@dataclass
class Tool:
    """A named action the model can call, with a JSON schema for its arguments."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., ActionResult]

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def __call__(self, **kwargs: Any) -> ActionResult:
        try:
            result = self.handler(**kwargs)
        except Exception as e:  # last line of defence; bad model arguments land here too
            return ActionResult.fail(f"{type(e).__name__}: {e}")
        return result if isinstance(result, ActionResult) else ActionResult(True, str(result))


def schema(required: tuple[str, ...] = (), **properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


def retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 15.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    retry_if: Callable[[T], bool] | None = None,
    on_retry: Callable[[int, Any, float], None] | None = None,
) -> T:
    """Call fn() with exponential backoff and jitter.

    Retries when fn raises one of `retry_on`, or returns a value for which
    `retry_if(value)` is true. When attempts run out the last exception is
    re-raised, or the last value returned.
    """
    for attempt in range(1, attempts + 1):
        try:
            result = fn()
        except retry_on as e:
            if attempt == attempts:
                raise
            reason: Any = e
        else:
            if retry_if is None or attempt == attempts or not retry_if(result):
                return result
            reason = result
        delay = min(max_delay, base_delay * 2 ** (attempt - 1))
        delay = random.uniform(delay / 2, delay)
        if on_retry:
            on_retry(attempt, reason, delay)
        time.sleep(delay)
    raise AssertionError("unreachable")
