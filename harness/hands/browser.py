"""Browser hand: Playwright-driven Chromium/Chrome with a persistent profile.

Elements are addressed by `ref` (numbers from the latest DOM observation), by a
Playwright selector, by visible text, or by x/y in screenshot coordinates.
Transport-level failures (timeouts, dropped connections, 5xx/429) are retried
here. Deciding what to do about a page that did the wrong thing belongs to the loop.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from ..eyes.screen import MAX_EDGE, Geometry
from .base import ActionResult, Tool, retry, schema, truncate

RETRY_STATUS = {429, 500, 502, 503, 504}


def _short(e: BaseException, limit: int = 700) -> str:
    """Playwright errors end with a call log of retries. Keep the headline plus the last few
    distinct log lines: that is where the reason is (e.g. "<div class=loader> intercepts pointer events")."""
    lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
    head, log = lines[:1], []
    for ln in reversed(lines[1:]):
        if ln not in log and not ln.startswith(("Call log", "=====")):
            log.insert(0, ln)
        if len(log) == 6:
            break
    return truncate("\n".join(head + log), limit)


class BrowserHand:
    def __init__(
        self,
        profile_dir: str | Path = "~/.harness/browser-profile",
        channel: str | None = None,  # "chrome" uses the installed Google Chrome instead of bundled Chromium
        cdp_url: str | None = None,  # attach to a Chrome you started with --remote-debugging-port
        headless: bool = False,
        viewport: tuple[int, int] | None = (1280, 800),
        downloads_dir: str | Path = "runs/downloads",
        nav_timeout: float = 45,
        action_timeout: float = 10,
        nav_retries: int = 3,
        max_edge: int = MAX_EDGE,
    ):
        self.profile_dir = Path(profile_dir).expanduser()
        self.channel = channel
        self.cdp_url = cdp_url
        self.headless = headless
        self.viewport = viewport
        self.downloads_dir = Path(downloads_dir)
        self.nav_timeout = nav_timeout
        self.action_timeout = action_timeout
        self.nav_retries = nav_retries
        self.max_edge = max_edge
        self.events: list[str] = []  # dialogs, new tabs, downloads, retries; drained by the Eyes
        self._pw = self._browser = self._context = self._page = None

    # ---------- lifecycle ----------

    @property
    def started(self) -> bool:
        return self._context is not None

    def start(self) -> None:
        if self.started:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if self.cdp_url:
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
            self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        else:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                channel=self.channel,
                headless=self.headless,
                viewport={"width": self.viewport[0], "height": self.viewport[1]} if self.viewport else None,
                no_viewport=self.viewport is None,
                accept_downloads=True,
            )
        self._context.set_default_timeout(self.action_timeout * 1000)
        self._context.set_default_navigation_timeout(self.nav_timeout * 1000)
        for p in self._context.pages:
            self._wire(p)
        self._context.on("page", self._adopt)
        self._page = self._context.pages[-1] if self._context.pages else self._context.new_page()

    def close(self) -> None:
        try:
            if self._context and not self.cdp_url:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        finally:
            self._pw = self._browser = self._context = self._page = None

    @property
    def page(self):
        self.start()
        if self._page is None or self._page.is_closed():
            open_pages = [p for p in self._context.pages if not p.is_closed()]
            self._page = open_pages[-1] if open_pages else self._context.new_page()
        return self._page

    def _wire(self, page) -> None:
        page.on("dialog", self._on_dialog)
        page.on("download", self._on_download)

    def _adopt(self, page) -> None:
        # Popups and target=_blank links (e.g. payment gateways) become the active tab.
        self._wire(page)
        self._page = page
        self.events.append(f"new tab opened and focused: {page.url or '(loading)'}")

    def _on_dialog(self, dialog) -> None:
        self.events.append(f'{dialog.type} dialog said: "{dialog.message}" (accepted)')
        try:
            dialog.accept()
        except Exception:
            pass

    def _on_download(self, download) -> None:
        target = self.downloads_dir / download.suggested_filename
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(target)
            self.events.append(f"downloaded file saved to {target}")
        except Exception as e:
            self.events.append(f"download failed: {e}")

    def drain_events(self) -> list[str]:
        events, self.events = self.events, []
        return events

    # ---------- helpers ----------

    def geometry(self) -> Geometry:
        size = self.page.viewport_size or self.page.evaluate("({width: innerWidth, height: innerHeight})")
        return Geometry.for_size(size["width"], size["height"], max_edge=self.max_edge)

    def _locate(self, ref: int | None = None, selector: str | None = None, text: str | None = None):
        page = self.page
        if ref is not None:
            for frame in page.frames:
                loc = frame.locator(f'[data-agent-id="{int(ref)}"]')
                try:
                    if loc.count():
                        return loc.first
                except Exception:
                    continue
            raise LookupError(f"ref {ref} is no longer on the page (it changed). Observe again for fresh refs.")
        if selector:
            return page.locator(selector).first
        if text:
            return page.get_by_text(text).filter(visible=True).first
        raise ValueError("give one of: ref, selector, text (or x and y)")

    @staticmethod
    def _what(ref, selector, text) -> str:
        if ref is not None:
            return f"ref {ref}"
        return f"selector {selector!r}" if selector else f"text {text!r}"

    def _settle(self, ms: int = 300) -> None:
        """Give an SPA a moment to react, and wait out a navigation if one started."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            self.page.wait_for_timeout(ms)
        except Exception:
            pass

    def _do(self, fn: Callable[[], str], settle: bool = False) -> ActionResult:
        from playwright.sync_api import Error as PWError

        try:
            msg = fn()
        except (LookupError, ValueError) as e:
            return ActionResult.fail(str(e))
        except PWError as e:
            return ActionResult.fail(_short(e))
        if settle:
            self._settle()
        return ActionResult(True, msg, data={"url": self.page.url})

    # ---------- actions ----------

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> ActionResult:
        from playwright.sync_api import Error as PWError

        if "://" not in url and not url.startswith(("about:", "data:")):
            url = "https://" + url

        def note(attempt: int, reason: Any, delay: float) -> None:
            why = f"HTTP {reason.status}" if hasattr(reason, "status") else _short(reason, 200)
            self.events.append(f"goto {url} attempt {attempt} failed ({why}); retrying in {delay:.1f}s")

        try:
            resp = retry(
                lambda: self.page.goto(url, wait_until=wait_until),
                attempts=self.nav_retries, base_delay=2, retry_on=(PWError,),
                retry_if=lambda r: r is not None and r.status in RETRY_STATUS, on_retry=note,
            )
        except PWError as e:
            return ActionResult.fail(f"could not load {url} after {self.nav_retries} attempts: {_short(e)}")
        status = resp.status if resp else None
        result = ActionResult(True, f"loaded {self.page.url} (HTTP {status})", data={"url": self.page.url, "status": status})
        if status and status >= 400:
            result.ok, result.error = False, f"HTTP {status}"
        return result

    def click(self, ref: int | None = None, selector: str | None = None, text: str | None = None,
              x: float | None = None, y: float | None = None, button: str = "left",
              double: bool = False, force: bool = False, timeout: float | None = None) -> ActionResult:
        def act() -> str:
            count = 2 if double else 1
            if x is not None and y is not None:
                cx, cy = self.geometry().to_input(x, y)
                self.page.mouse.click(cx, cy, button=button, click_count=count)
                return f"clicked at ({x}, {y})"
            loc = self._locate(ref, selector, text)
            what = self._what(ref, selector, text)
            ms = (timeout or self.action_timeout) * 1000
            if not force:
                loc.click(button=button, click_count=count, timeout=ms)
                return f"clicked {what}"
            # A forced *mouse* click still lands on whatever covers the element, so dispatch
            # the click on the element itself.
            loc.evaluate("el => { el.scrollIntoView({block: 'center'}); el.focus && el.focus(); el.click(); }")
            return f"clicked {what} via JavaScript, bypassing anything covering it"

        return self._do(act, settle=True)

    def type(self, text: str, ref: int | None = None, selector: str | None = None, clear: bool = True,
             submit: bool = False, delay_ms: int = 0) -> ActionResult:
        def act() -> str:
            if ref is None and selector is None:
                self.page.keyboard.type(text, delay=delay_ms)
                target = "the focused element"
            else:
                loc = self._locate(ref, selector)
                target = self._what(ref, selector, None)
                if delay_ms:  # key-by-key, for autocompletes that listen to keyup
                    loc.click()
                    if clear:
                        loc.fill("")
                    loc.press_sequentially(text, delay=delay_ms)
                elif clear:
                    loc.fill(text)
                else:
                    loc.press_sequentially(text)
            if submit:
                self.page.keyboard.press("Enter")
            # The text itself is not echoed: it may be a password.
            return f"typed {len(text)} chars into {target}" + (" and pressed Enter" if submit else "")

        return self._do(act, settle=submit)

    def select(self, ref: int | None = None, selector: str | None = None,
               value: str | None = None, label: str | None = None) -> ActionResult:
        def act() -> str:
            loc = self._locate(ref, selector)
            chosen = loc.select_option(value=value) if value is not None else loc.select_option(label=label)
            return f"selected {chosen} in {self._what(ref, selector, None)}"

        return self._do(act, settle=True)

    def press(self, key: str, ref: int | None = None) -> ActionResult:
        def act() -> str:
            if ref is not None:
                self._locate(ref).press(key)
            else:
                self.page.keyboard.press(key)
            return f"pressed {key}"

        return self._do(act, settle=True)

    def scroll(self, dy: int = 600, dx: int = 0, ref: int | None = None) -> ActionResult:
        def act() -> str:
            if ref is not None:
                self._locate(ref).scroll_into_view_if_needed()
                return f"scrolled ref {ref} into view"
            g = self.geometry()
            self.page.mouse.move(g.width / 2, g.height / 2)
            self.page.mouse.wheel(dx, dy)
            self.page.wait_for_timeout(300)
            pos = self.page.evaluate("[scrollX, scrollY, document.documentElement.scrollHeight - innerHeight]")
            return f"scrolled by ({dx}, {dy}); page offset now y={pos[1]:.0f} of max {pos[2]:.0f}"

        return self._do(act)

    def wait(self, text: str | None = None, selector: str | None = None, url_contains: str | None = None,
             gone: bool = False, timeout: float = 15) -> ActionResult:
        def act() -> str:
            ms = timeout * 1000
            state = "hidden" if gone else "visible"
            if url_contains:
                self.page.wait_for_url(lambda u: url_contains in u, timeout=ms)
                return f"url now contains {url_contains!r}"
            if text:
                loc = self.page.get_by_text(text)
                (loc.first if gone else loc.filter(visible=True).first).wait_for(state=state, timeout=ms)
            elif selector:
                self.page.locator(selector).first.wait_for(state=state, timeout=ms)
            else:
                self.page.wait_for_load_state("networkidle", timeout=ms)
                return "network idle"
            return f"{text or selector!r} is {'gone' if gone else 'visible'}"

        started = time.monotonic()
        res = self._do(act)
        res.data["waited_s"] = round(time.monotonic() - started, 2)
        return res

    def extract(self, ref: int | None = None, selector: str | None = None, max_chars: int = 15_000) -> ActionResult:
        def act() -> str:
            if ref is None and selector is None:
                return truncate(self.page.inner_text("body"), max_chars)
            return truncate(self._locate(ref, selector).inner_text(), max_chars)

        return self._do(act)

    def eval_js(self, script: str) -> ActionResult:
        return self._do(lambda: truncate(json.dumps(self.page.evaluate(script), default=str, ensure_ascii=False), 15_000))

    def back(self) -> ActionResult:
        return self._do(lambda: (self.page.go_back(), f"went back to {self.page.url}")[1], settle=True)

    def reload(self) -> ActionResult:
        return self._do(lambda: (self.page.reload(), f"reloaded {self.page.url}")[1], settle=True)

    def tabs(self) -> list[dict]:
        current = self.page
        pages = [p for p in self._context.pages if not p.is_closed()]
        return [{"index": i, "url": p.url, "title": _safe_title(p), "active": p is current} for i, p in enumerate(pages)]

    def switch_tab(self, index: int) -> ActionResult:
        def act() -> str:
            pages = [p for p in self._context.pages if not p.is_closed()]
            self._page = pages[index]
            self._page.bring_to_front()
            return f"switched to tab {index}: {self._page.url}"

        return self._do(act)

    def new_tab(self, url: str | None = None) -> ActionResult:
        self.start()
        self._context.new_page()  # _adopt makes it active
        return self.goto(url) if url else ActionResult(True, "opened a blank tab")

    def save_screenshot(self, path: str, full_page: bool = True) -> ActionResult:
        def act() -> str:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(p), full_page=full_page, timeout=20_000)
            return f"saved screenshot to {p}"

        return self._do(act)

    def tools(self) -> list[Tool]:
        ref = {"type": "integer", "description": "element number from the latest observation"}
        sel = {"type": "string", "description": "Playwright selector, e.g. css=#id, text=Login, role=button[name='OK']"}
        return [
            Tool("browser_goto", "Open a URL in the active tab. Retries automatically on network errors and 429/5xx.",
                 schema(("url",), url={"type": "string"}), self.goto),
            Tool("browser_click", "Click an element by ref, selector or visible text, or click at x,y in screenshot "
                 "pixels. If something covers the element, wait for it to go away; force=true clicks anyway.",
                 schema((), ref=ref, selector=sel, text={"type": "string"}, x={"type": "number"}, y={"type": "number"},
                        double={"type": "boolean"}, force={"type": "boolean"},
                        timeout={"type": "number", "description": "seconds"}), self.click),
            Tool("browser_type", "Type into an input by ref or selector (replaces its content unless clear=false). "
                 "With no target, types into the focused element. delay_ms>0 types key by key, for autocompletes. "
                 "submit=true presses Enter afterwards.",
                 schema(("text",), text={"type": "string"}, ref=ref, selector=sel, clear={"type": "boolean"},
                        submit={"type": "boolean"}, delay_ms={"type": "integer"}), self.type),
            Tool("browser_select", "Choose an option in a native <select> by value or by visible label.",
                 schema((), ref=ref, selector=sel, value={"type": "string"}, label={"type": "string"}), self.select),
            Tool("browser_press", "Press a key or chord (Enter, Escape, ArrowDown, Tab, Control+A), optionally on an element.",
                 schema(("key",), key={"type": "string"}, ref=ref), self.press),
            Tool("browser_scroll", "Scroll the page by dy pixels (negative scrolls up), or bring an element into view.",
                 schema((), dy={"type": "integer"}, dx={"type": "integer"}, ref=ref), self.scroll),
            Tool("browser_wait", "Wait until text or a selector appears (or is gone, with gone=true), the URL "
                 "contains a string, or, with no arguments, the network is idle.",
                 schema((), text={"type": "string"}, selector=sel, url_contains={"type": "string"},
                        gone={"type": "boolean"}, timeout={"type": "number", "description": "seconds, default 15"}),
                 self.wait),
            Tool("browser_extract", "Return the full text of an element, or of the whole page.",
                 schema((), ref=ref, selector=sel, max_chars={"type": "integer"}), self.extract),
            Tool("browser_eval", "Run a JavaScript expression or function in the page and return its JSON result.",
                 schema(("script",), script={"type": "string"}), self.eval_js),
            Tool("browser_back", "Go back in history.", schema(), self.back),
            Tool("browser_reload", "Reload the page.", schema(), self.reload),
            Tool("browser_switch_tab", "Make another open tab active (indexes are listed in the observation).",
                 schema(("index",), index={"type": "integer"}), self.switch_tab),
            Tool("browser_new_tab", "Open a new tab, optionally at a URL.", schema((), url={"type": "string"}), self.new_tab),
            Tool("browser_save_screenshot", "Save a screenshot of the page to a file, e.g. as proof of a result.",
                 schema(("path",), path={"type": "string"}, full_page={"type": "boolean"}), self.save_screenshot),
        ]


def _safe_title(page) -> str:
    try:
        return page.title()
    except Exception:
        return ""
