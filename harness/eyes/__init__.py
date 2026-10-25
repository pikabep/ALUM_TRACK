"""Eyes: everything the agent can perceive: screenshots, the page DOM, terminal output."""
from __future__ import annotations

import io
from typing import TYPE_CHECKING

from PIL import Image

from ..hands.base import retry
from .dom import DomSnapshot, snapshot
from .observation import Observation
from .screen import MAX_EDGE, Geometry, Shot, annotate, capture_desktop, diff_ratio, fit, from_image

if TYPE_CHECKING:
    from ..hands.browser import BrowserHand

__all__ = ["Eyes", "Observation", "Shot", "Geometry", "DomSnapshot", "MAX_EDGE", "capture_desktop", "diff_ratio", "fit"]


class Eyes:
    def __init__(self, max_edge: int = MAX_EDGE, max_elements: int = 150, max_text: int = 6000, set_of_marks: bool = True):
        self.max_edge = max_edge
        self.max_elements = max_elements
        self.max_text = max_text
        self.set_of_marks = set_of_marks  # draw numbered boxes on browser screenshots

    def desktop(self) -> Shot:
        return capture_desktop(max_edge=self.max_edge)

    def browser(self, hand: "BrowserHand", with_screenshot: bool = True) -> tuple[DomSnapshot, Shot | None]:
        page = hand.page
        snap = snapshot(page, self.max_elements, self.max_text)
        shot = None
        if with_screenshot:
            try:
                # scale="css" keeps image pixels == CSS pixels before resizing, matching click coordinates
                png = retry(lambda: page.screenshot(type="png", scale="css", timeout=15_000),
                            attempts=3, base_delay=0.5)  # fresh or busy windows sometimes refuse once
                shot = from_image(Image.open(io.BytesIO(png)), hand.geometry(), "browser")
                if self.set_of_marks and snap.boxes:
                    shot = annotate(shot, snap.boxes)
            except Exception as e:
                snap.notes.append(f"(browser screenshot failed: {str(e).splitlines()[0]})")
        return snap, shot

    def observe(self, browser: "BrowserHand | None" = None, mode: str = "auto",
                terminal: str | None = None, notes: str = "") -> Observation:
        """mode: "browser" (DOM + page screenshot), "desktop" (full screen), "both",
        "text" (DOM only, no image: fast and cheap), or "auto" (browser if one is open, else desktop)."""
        browser_open = browser is not None and browser.started
        if mode == "auto":
            mode = "browser" if browser_open else "desktop"
        sections: list[str] = []
        shots: list[Shot] = []

        if mode in ("browser", "both", "text") and browser_open:
            try:
                snap, shot = self.browser(browser, with_screenshot=mode != "text")
            except Exception as e:  # crashed or closed browser: report it and look at the screen instead
                sections.append(f"## Browser\n(could not read the browser: {str(e).splitlines()[0]})")
                mode = "desktop"
            else:
                tabs = browser.tabs()
                if len(tabs) > 1:
                    sections.append("## Tabs\n" + "\n".join(
                        f"[{t['index']}] {t['title'][:60]!r} {t['url'][:100]}{'  <- active' if t['active'] else ''}"
                        for t in tabs))
                events = browser.drain_events()
                if events:
                    sections.append("## Browser events since last look\n" + "\n".join(f"- {e}" for e in events))
                sections.append("## Browser\n" + snap.to_text())
                if shot:
                    shots.append(shot)

        if mode in ("desktop", "both") or (mode == "text" and not browser_open):
            try:
                shot = self.desktop()
                shots.append(shot)
                sections.append(f"## Desktop\nScreenshot is {shot.width}x{shot.height}px; desktop_* tools take x,y in these pixels.")
            except Exception as e:
                sections.append(f"## Desktop\n(screen capture failed: {e})")

        if terminal:
            sections.append("## Last terminal output\n" + terminal)
        if notes:
            sections.append(notes)
        return Observation("\n\n".join(sections), shots)
