"""Hands: everything the agent can do to the computer."""
from __future__ import annotations

from .base import ActionResult, Tool, retry
from .browser import BrowserHand
from .desktop import DesktopHand
from .files import FileHand
from .shell import ShellHand

__all__ = ["ActionResult", "Tool", "retry", "Hands", "BrowserHand", "DesktopHand", "FileHand", "ShellHand"]


class Hands:
    """Bundles the hands and exposes their tools by name for the loop."""

    def __init__(self, shell: ShellHand | None = None, files: FileHand | None = None,
                 browser: BrowserHand | None = None, desktop: DesktopHand | None = None):
        self.shell = shell or ShellHand()
        self.files = files or FileHand()
        self.browser = browser or BrowserHand()
        self.desktop = desktop or DesktopHand()

    def tools(self) -> dict[str, Tool]:
        tools = self.shell.tools() + self.files.tools() + self.browser.tools() + self.desktop.tools()
        return {t.name: t for t in tools}

    def close(self) -> None:
        self.browser.close()
        self.shell.close()
