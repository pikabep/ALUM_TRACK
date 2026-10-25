"""Companion app server: one calm chat screen where Guard narrates, asks and shows its work.

Standard library only, so nothing extra can break on the day:
  http://127.0.0.1:7777/#<token>   the app (bound to localhost only)
  GET  /events?token=              server-sent events (history replayed on connect)
  POST /api/command                {token, cmd: send|stop, text}
  GET  /api/state?token=           readiness checks and countdown target
  GET  /files/<run>/<path>         screenshots and reports from the runs folder
  POST :7778/inbox                 OTP forwarder from the phone (only when INBOX_SECRET is set;
                                   this second port listens on the LAN, guarded by the secret)
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import secrets
import shutil
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .bus import EventBus
from .inbox import Inbox

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


class WebChannel:
    """Handoff channel backed by the app: questions and approvals become cards in the chat."""

    name = "app"
    usable = True

    def __init__(self, bus: EventBus, files_root: Path):
        self.bus = bus
        self.files_root = files_root
        self.answers: queue.Queue[str] = queue.Queue()
        self.pending: int | None = None
        self._n = 0

    def send(self, text: str, image: bytes | None = None, urgent: bool = False) -> None:
        if not urgent:
            self.bus.publish("say", text=text, system=True)
            return
        self._n += 1
        url = None
        if image:
            d = self.files_root / "_app"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"ask-{self._n}.png").write_bytes(image)
            url = f"/files/_app/ask-{self._n}.png"
        kind = "approval" if text.startswith("APPROVAL NEEDED") else "question"
        body = text.removeprefix("APPROVAL NEEDED\n").replace("\n\nReply YES to approve, anything else to refuse.", "")
        self.pending = self._n
        self.bus.publish("ask", ask_id=self._n, kind=kind, text=body, image=url)

    def start_listening(self) -> None:
        while not self.answers.empty():
            self.answers.get_nowait()

    def poll(self) -> str | None:
        try:
            answer = self.answers.get_nowait()
        except queue.Empty:
            return None
        self.bus.publish("answered", ask_id=self.pending, answer=answer, by="app")
        self.pending = None
        return answer

    def answered(self, answer: str, by: str) -> None:
        self.bus.publish("answered", ask_id=self.pending, answer=answer, by=by)
        self.pending = None


class App:
    """Runs tasks typed in the chat on one worker thread (Playwright objects stay on one thread)."""

    def __init__(self, bus: EventBus, channel: WebChannel, inbox: Inbox, make_agent: Callable[[], object],
                 countdown_to: str = "11:00:00", checks: Callable[[], list[dict]] | None = None):
        self.bus, self.channel, self.inbox = bus, channel, inbox
        self.make_agent = make_agent
        self.agent = None
        self.tasks: queue.Queue[str] = queue.Queue()
        self.running = False
        self.countdown_to = countdown_to
        self.checks = checks or (lambda: [])

    def start_worker(self) -> None:
        threading.Thread(target=self._work, daemon=True, name="agent").start()

    def _work(self) -> None:
        while True:
            task = self.tasks.get()
            self.running = True
            try:
                if self.agent is None:
                    self.agent = self.make_agent()
                self.agent.run(task)
            except Exception as e:  # never let one bad run kill the app
                self.bus.publish("done", success=False, summary=f"I hit an internal error: {e}", handoffs=0)
            finally:
                self.running = False
                self.bus.publish("status", state="idle")

    def command(self, cmd: str, text: str = "") -> dict:
        text = text.strip()
        if cmd == "stop":
            if self.agent is not None:
                self.agent.stop_requested = True
            self.bus.publish("say", text="Stopping after this step.", system=True)
            return {"ok": True}
        if cmd != "send" or not text:
            return {"ok": False, "error": "unknown command"}
        self.bus.publish("user", text=text)
        if self.channel.pending is not None:
            self.channel.answers.put(text)
        elif self.running and self.agent is not None:
            self.agent.messages.put(text)
            self.bus.publish("say", text="Got it. I'll take that into account on my next step.", system=True)
        else:
            self.tasks.put(text)
        return {"ok": True}

    def attach(self, agent) -> None:
        """For a run started outside the app (CLI or schedule): let the chat steer it."""
        self.agent, self.running = agent, True


def _handler(app: App, token: str, files_root: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode())

        def _authed(self, q: dict) -> bool:
            return secrets.compare_digest((q.get("token") or [""])[0], token)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                return self._send(200, (UI_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/events":
                return self._events() if self._authed(q) else self._json({"error": "bad token"}, 403)
            if url.path == "/api/state":
                if not self._authed(q):
                    return self._json({"error": "bad token"}, 403)
                return self._json({"countdown_to": app.countdown_to, "running": app.running,
                                   "checks": app.checks(), "now": time.time()})
            if url.path.startswith("/files/"):
                return self._file(files_root, url.path[len("/files/"):])
            if url.path.startswith("/ui/"):
                return self._file(UI_DIR, url.path[len("/ui/"):])
            self._json({"error": "not found"}, 404)

        def _file(self, root: Path, rel: str) -> None:
            p = (root / rel).resolve()
            if not p.is_file() or root.resolve() not in p.parents:
                return self._json({"error": "not found"}, 404)
            self._send(200, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/command":
                return self._json({"error": "not found"}, 404)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            if not secrets.compare_digest(str(body.get("token", "")), token):
                return self._json({"error": "bad token"}, 403)
            self._json(app.command(str(body.get("cmd", "")), str(body.get("text", ""))))

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q, history = app.bus.subscribe()
            try:
                for e in history:
                    self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        e = q.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")  # keeps the connection alive
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                app.bus.unsubscribe(q)
                self.close_connection = True

    return Handler


def _inbox_handler(inbox: Inbox, secret: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0).decode("utf-8", "replace")
            q = parse_qs(urlparse(self.path).query)
            try:
                data = json.loads(raw) if raw.strip().startswith("{") else {k: v[0] for k, v in parse_qs(raw).items()}
            except ValueError:
                data = {"text": raw}
            given = str(data.get("secret") or (q.get("secret") or [""])[0] or self.headers.get("X-Secret", ""))
            if not secrets.compare_digest(given, secret):
                self.send_response(403)
                self.end_headers()
                return
            text = str(data.get("text") or data.get("message") or data.get("msg") or data.get("body") or raw)
            inbox.add(text, str(data.get("from") or data.get("sender") or ""))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        do_GET = do_POST  # some forwarder apps can only send GET with query parameters

    return Handler


def readiness(cfg: dict) -> list[dict]:
    """Cheap, local checks for the countdown card. Each red item says exactly what to fix."""
    from .llm import PRESETS

    models = cfg.get("models", {})
    keys = [(models.get(n) or {}).get("key_env") or PRESETS.get((models.get(n) or {}).get("preset", n), {}).get("key_env")
            for n in models.get("chain", [])]
    have_model = any(k and os.environ.get(k) for k in keys)
    chrome = cfg.get("browser", {}).get("channel") != "chrome" or any(
        Path(p).exists() for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                   r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                                   "/Applications/Google Chrome.app"]) or shutil.which("google-chrome")
    locks = list(Path(cfg.get("agent", {}).get("state_dir", "state")).glob("*.done.lock"))
    return [
        {"name": "Model key", "ok": bool(have_model), "fix": "Add GEMINI_API_KEY (or GROQ/OPENROUTER) to .env"},
        {"name": "IRCTC login", "ok": bool(os.environ.get("IRCTC_USERNAME") and os.environ.get("IRCTC_PASSWORD")),
         "fix": "Set IRCTC_USERNAME and IRCTC_PASSWORD in .env"},
        {"name": "Passenger", "ok": Path("data/passenger.txt").is_file(),
         "fix": "Copy data/passenger.example.txt to data/passenger.txt and fill it in"},
        {"name": "Chrome", "ok": bool(chrome), "fix": "Install Google Chrome"},
        {"name": "Phone OTP link", "ok": bool(os.environ.get("INBOX_SECRET")),
         "fix": "Optional: set INBOX_SECRET and point your SMS forwarder at :7778/inbox"},
        {"name": "Telegram", "ok": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
         "fix": "Optional: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID mirror approvals to your phone"},
        {"name": "Not booked yet", "ok": not locks, "fix": f"{locks[0] if locks else ''} exists: already booked"},
    ]


def serve(app: App, files_root: Path, host: str = "127.0.0.1", port: int = 7777, open_browser: bool = True) -> str:
    token = os.environ.get("APP_TOKEN") or secrets.token_urlsafe(16)
    files_root.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), _handler(app, token, files_root))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="app-server").start()
    secret = os.environ.get("INBOX_SECRET")
    if secret:
        inbox_httpd = ThreadingHTTPServer(("0.0.0.0", port + 1), _inbox_handler(app.inbox, secret))
        threading.Thread(target=inbox_httpd.serve_forever, daemon=True, name="inbox-server").start()
    url = f"http://{host}:{port}/#{token}"
    print(f"Companion app: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    app.bus.publish("say", text=f"Morning! It's {datetime.now():%H:%M}. Tell me what to do, or let the schedule start me.",
                    system=True)
    return url
