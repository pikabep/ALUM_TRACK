"""File hand: read, write, append, edit and list files."""
from __future__ import annotations

import os
from pathlib import Path

from .base import ActionResult, Tool, schema


class FileHand:
    def __init__(self, root: str | Path | None = None, max_read: int = 50_000):
        self.root = Path(root or os.getcwd()).expanduser().resolve()
        self.max_read = max_read

    def _path(self, path: str) -> Path:
        p = Path(path).expanduser()
        return p if p.is_absolute() else self.root / p

    def read(self, path: str, start_line: int | None = None, end_line: int | None = None) -> ActionResult:
        p = self._path(path)
        if not p.is_file():
            return ActionResult.fail(f"no such file: {p}")
        text = p.read_text(encoding="utf-8", errors="replace")
        if start_line or end_line:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[(start_line or 1) - 1 : end_line])
        if len(text) > self.max_read:
            text = text[: self.max_read] + f"\n... [truncated at {self.max_read} chars; use start_line/end_line]"
        return ActionResult(True, text, data={"path": str(p)})

    def write(self, path: str, content: str) -> ActionResult:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, p)  # atomic: a crash never leaves a half-written file
        return ActionResult(True, f"wrote {len(content)} chars to {p}", data={"path": str(p)})

    def append(self, path: str, content: str) -> ActionResult:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(content)
        return ActionResult(True, f"appended {len(content)} chars to {p}", data={"path": str(p)})

    def edit(self, path: str, old: str, new: str, replace_all: bool = False) -> ActionResult:
        p = self._path(path)
        if not p.is_file():
            return ActionResult.fail(f"no such file: {p}")
        text = p.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            return ActionResult.fail("`old` text not found in file")
        if count > 1 and not replace_all:
            return ActionResult.fail(f"`old` text occurs {count} times; add context or set replace_all")
        return self.write(path, text.replace(old, new))

    def list(self, path: str = ".", pattern: str = "*", recursive: bool = False, limit: int = 500) -> ActionResult:
        p = self._path(path)
        if not p.is_dir():
            return ActionResult.fail(f"no such directory: {p}")
        matches = sorted(p.rglob(pattern) if recursive else p.glob(pattern))
        lines = []
        for m in matches[:limit]:
            rel = m.relative_to(p)
            lines.append(f"{rel}/" if m.is_dir() else f"{rel}  ({m.stat().st_size} bytes)")
        if len(matches) > limit:
            lines.append(f"... {len(matches) - limit} more")
        return ActionResult(True, "\n".join(lines) or "(empty)", data={"path": str(p)})

    def tools(self) -> list[Tool]:
        path = {"type": "string", "description": "absolute, ~-relative, or relative to the agent's root"}
        return [
            Tool("file_read", "Read a text file, optionally a 1-based inclusive line range.",
                 schema(("path",), path=path, start_line={"type": "integer"}, end_line={"type": "integer"}),
                 self.read),
            Tool("file_write", "Create or overwrite a text file (parent folders are created).",
                 schema(("path", "content"), path=path, content={"type": "string"}), self.write),
            Tool("file_append", "Append text to a file.",
                 schema(("path", "content"), path=path, content={"type": "string"}), self.append),
            Tool("file_edit", "Replace an exact snippet of text in a file. `old` must match exactly once "
                 "unless replace_all is true.",
                 schema(("path", "old", "new"), path=path, old={"type": "string"}, new={"type": "string"},
                        replace_all={"type": "boolean"}), self.edit),
            Tool("file_list", "List a directory, with an optional glob pattern.",
                 schema((), path=path, pattern={"type": "string"}, recursive={"type": "boolean"}), self.list),
        ]
