"""Desktop hand: OS-level mouse and keyboard via PyAutoGUI, for any app.

Coordinates are in screenshot pixels (what the model saw); they are mapped to
real screen coordinates with the same Geometry the Eyes used to resize the shot.
PyAutoGUI's fail-safe stays on: slam the mouse into a screen corner to abort.
"""
from __future__ import annotations

import sys
from functools import cached_property

from ..eyes.screen import MAX_EDGE, Geometry, desktop_geometry
from .base import ActionResult, Tool, schema

_IS_MAC = sys.platform == "darwin"
_KEY_ALIASES = {
    "mod": "command" if _IS_MAC else "ctrl",  # the platform's main shortcut key
    "cmd": "command",
    "control": "ctrl",
    "option": "alt",
    "super": "command" if _IS_MAC else "win",
    "meta": "command" if _IS_MAC else "win",
    "return": "enter",
    "escape": "esc",
}


class DesktopHand:
    def __init__(self, monitor: int = 1, max_edge: int = MAX_EDGE):
        self.monitor = monitor
        self.max_edge = max_edge

    @cached_property
    def gui(self):
        import pyautogui  # imported lazily: it needs a display at import time

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        return pyautogui

    def geometry(self) -> Geometry:
        return desktop_geometry(self.monitor, self.max_edge)

    def _xy(self, x: float, y: float) -> tuple[int, int]:
        g = self.geometry()
        if not (0 <= x <= g.img_w and 0 <= y <= g.img_h):
            raise ValueError(f"({x}, {y}) is outside the {g.img_w}x{g.img_h} screenshot")
        return g.to_input(x, y)

    def _do(self, fn) -> ActionResult:
        try:
            return ActionResult(True, fn())
        except Exception as e:
            return ActionResult.fail(f"{type(e).__name__}: {e}")

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> ActionResult:
        def act() -> str:
            sx, sy = self._xy(x, y)
            self.gui.click(sx, sy, clicks=clicks, interval=0.08, button=button)
            return f"{button}-clicked x{clicks} at ({x}, {y})"

        return self._do(act)

    def move(self, x: float, y: float) -> ActionResult:
        def act() -> str:
            self.gui.moveTo(*self._xy(x, y), duration=0.1)
            return f"moved mouse to ({x}, {y})"

        return self._do(act)

    def drag(self, x1: float, y1: float, x2: float, y2: float, button: str = "left") -> ActionResult:
        def act() -> str:
            self.gui.moveTo(*self._xy(x1, y1))
            self.gui.dragTo(*self._xy(x2, y2), duration=0.4, button=button)
            return f"dragged ({x1}, {y1}) -> ({x2}, {y2})"

        return self._do(act)

    def type(self, text: str, interval: float = 0.02) -> ActionResult:
        def act() -> str:
            if text.isascii():
                self.gui.write(text, interval=interval)
            else:  # PyAutoGUI cannot type non-ASCII; paste it instead
                import pyperclip

                pyperclip.copy(text)
                self.gui.hotkey(_key("mod"), "v")
            return f"typed {len(text)} chars"

        return self._do(act)

    def hotkey(self, keys: list[str]) -> ActionResult:
        def act() -> str:
            names = [_key(k) for k in keys]
            self.gui.hotkey(*names)
            return f"pressed {'+'.join(names)}"

        return self._do(act)

    def press(self, key: str, presses: int = 1) -> ActionResult:
        return self._do(lambda: (self.gui.press(_key(key), presses=presses, interval=0.05), f"pressed {key} x{presses}")[1])

    def scroll(self, amount: int, x: float | None = None, y: float | None = None) -> ActionResult:
        def act() -> str:
            if x is not None and y is not None:
                self.gui.moveTo(*self._xy(x, y))
            self.gui.scroll(amount)
            return f"scrolled {amount} clicks ({'up' if amount > 0 else 'down'})"

        return self._do(act)

    def cursor(self) -> tuple[int, int]:
        """Mouse position in screenshot pixels."""
        return self.geometry().to_image(*self.gui.position())

    def tools(self) -> list[Tool]:
        xy = {"x": {"type": "number", "description": "screenshot pixels"}, "y": {"type": "number"}}
        return [
            Tool("desktop_click", "Click anywhere on the screen at x,y in screenshot pixels. clicks=2 double-clicks.",
                 schema(("x", "y"), **xy, button={"type": "string", "enum": ["left", "right", "middle"]},
                        clicks={"type": "integer"}), self.click),
            Tool("desktop_move", "Move the mouse to x,y (screenshot pixels), e.g. to hover.",
                 schema(("x", "y"), **xy), self.move),
            Tool("desktop_drag", "Drag with the mouse from (x1, y1) to (x2, y2) in screenshot pixels.",
                 schema(("x1", "y1", "x2", "y2"), x1={"type": "number"}, y1={"type": "number"},
                        x2={"type": "number"}, y2={"type": "number"}), self.drag),
            Tool("desktop_type", "Type text with the keyboard into whatever has focus.",
                 schema(("text",), text={"type": "string"}), self.type),
            Tool("desktop_hotkey", "Press a key combination, e.g. [\"mod\", \"s\"]. `mod` is Cmd on macOS and Ctrl elsewhere.",
                 schema(("keys",), keys={"type": "array", "items": {"type": "string"}}), self.hotkey),
            Tool("desktop_press", "Press a single key (enter, esc, tab, up, down, f5, ...) one or more times.",
                 schema(("key",), key={"type": "string"}, presses={"type": "integer"}), self.press),
            Tool("desktop_scroll", "Scroll the mouse wheel: positive is up, negative is down; optionally at x,y first.",
                 schema(("amount",), amount={"type": "integer"}, **xy), self.scroll),
        ]


def _key(name: str) -> str:
    n = name.strip().lower()
    return _KEY_ALIASES.get(n, n)
