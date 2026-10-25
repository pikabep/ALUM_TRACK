"""Shell hand: run commands with a persistent working directory, timeouts and a safety guard."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .base import ActionResult, Tool, schema, truncate

# Commands that need a human's OK before they run.
DANGEROUS = [
    r"\brm\s+-\w*[rf]\w*\s+(/|~|\$HOME)(\s|$|/\*)",
    r"\bmkfs\b",
    r"\bdd\b.*\bof=/dev/",
    r":\(\)\s*\{.*\};\s*:",
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r">\s*/dev/(sd|nvme|disk)",
    r"\bgit\s+push\b.*--force",
]
_CWD_MARK = "__HARNESS_CWD__"


class ShellHand:
    def __init__(
        self,
        cwd: str | Path | None = None,
        timeout: float = 60,
        max_output: int = 20_000,
        confirm: Callable[[str], bool] | None = None,
    ):
        self.cwd = Path(cwd or os.getcwd()).expanduser().resolve()
        self.timeout = timeout
        self.max_output = max_output
        # confirm(question) -> bool. Wired to Human Handoff by the loop; None blocks risky commands.
        self.confirm = confirm
        self.last_output = ""
        self._posix = os.name == "posix"
        self._shell = (shutil.which("bash") or "/bin/sh") if self._posix else None
        self._bg: dict[int, tuple[subprocess.Popen, Path]] = {}

    def run(self, command: str, timeout: float | None = None, background: bool = False) -> ActionResult:
        if any(re.search(p, command) for p in DANGEROUS):
            question = f"The agent wants to run a risky command:\n  {command}\nAllow it?"
            if not (self.confirm and self.confirm(question)):
                return ActionResult.fail("blocked by safety guard: this command needs human approval")
        if background:
            return self._spawn(command)

        timeout = timeout or self.timeout
        if self._posix:
            # Print $PWD after the command so `cd` carries over to the next call.
            script = f'{command}\n__rc=$?; printf "\\n{_CWD_MARK}%s" "$PWD"; exit $__rc'
            args: str | list[str] = [self._shell, "-c", script]
        else:
            args = command
        proc = subprocess.Popen(
            args,
            cwd=self.cwd,
            shell=not self._posix,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            start_new_session=self._posix,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out = ""
            out = self._strip_cwd(out or "")
            self.last_output = out
            return ActionResult.fail(
                f"timed out after {timeout}s and was killed (use background=true for long-running programs)",
                truncate(out, self.max_output),
            )

        out = self._strip_cwd(out)
        self.last_output = out
        body = truncate(out, self.max_output)
        data = {"exit_code": proc.returncode, "cwd": str(self.cwd)}
        if proc.returncode != 0:
            return ActionResult.fail(f"exit code {proc.returncode}", body, **data)
        return ActionResult(True, body, data=data)

    def _strip_cwd(self, out: str) -> str:
        out, mark, cwd = out.rpartition("\n" + _CWD_MARK)
        if not mark:
            return cwd  # marker missing (command called `exit`, or not POSIX): whole output
        if cwd and Path(cwd).is_dir():
            self.cwd = Path(cwd)
        return out

    def _spawn(self, command: str) -> ActionResult:
        log = Path(tempfile.mkstemp(prefix="harness-bg-", suffix=".log")[1])
        with open(log, "w") as fh:
            proc = subprocess.Popen(
                [self._shell, "-c", command] if self._posix else command,
                cwd=self.cwd,
                shell=not self._posix,
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=self._posix,
            )
        self._bg[proc.pid] = (proc, log)
        return ActionResult(True, f"started in background, pid {proc.pid}, output -> {log}", data={"pid": proc.pid, "log": str(log)})

    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            if self._posix:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass

    def close(self) -> None:
        for proc, _ in self._bg.values():
            if proc.poll() is None:
                self._kill(proc)

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "shell_run",
                "Run a shell command and return its combined stdout/stderr. The working directory "
                "persists between calls (`cd` works). Use background=true to launch GUI apps or "
                "servers that keep running; their output goes to a log file you can read later.",
                schema(
                    ("command",),
                    command={"type": "string"},
                    timeout={"type": "number", "description": f"seconds, default {self.timeout:g}"},
                    background={"type": "boolean"},
                ),
                self.run,
            )
        ]
