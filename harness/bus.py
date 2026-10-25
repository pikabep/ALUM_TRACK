"""Event bus: the run publishes what it is doing; the companion app (and anyone else) listens.

The UI holds no truth. It only renders these events, and a reconnecting client gets
the recent history replayed, so a page reload loses nothing.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, history: int = 300):
        self._history: deque[dict] = deque(maxlen=history)
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def publish(self, event_type: str, /, **data: Any) -> dict:
        event = {"id": next(self._ids), "type": event_type, "ts": time.time(), **data}
        with self._lock:
            self._history.append(event)
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(event)
        return event

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
            return q, list(self._history)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


class NullBus(EventBus):
    """Used when no companion app is attached: events go nowhere."""

    def publish(self, event_type: str, /, **data: Any) -> dict:
        return {"type": event_type, **data}
