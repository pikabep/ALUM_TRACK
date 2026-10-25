"""Command line entry point.

  python -m harness app                      # companion chat app; type tasks there
  python -m harness run "task" [--skill NAME] [--at 10:50:00] [--when-file PATH] [--when-cmd CMD] [--no-app]
  python -m harness serve                    # chat trigger over Telegram
  python -m harness doctor                   # check display, browser and model providers before a run
  python -m harness skills                   # list skills
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .config import build_agent, build_model, load_config
from .skills import SkillLibrary
from .triggers import parse_when, serve_telegram, wait_for_command, wait_for_file, wait_until


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252; page text has ₹ and emoji
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="harness")
    ap.add_argument("--config", default="config.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a task now or on a trigger")
    r.add_argument("task")
    r.add_argument("--skill")
    r.add_argument("--max-steps", type=int)
    r.add_argument("--at", help="HH:MM[:SS] or ISO datetime")
    r.add_argument("--when-file", help="start when this path exists")
    r.add_argument("--when-cmd", help="start when this command exits 0 (polled)")
    r.add_argument("--no-app", action="store_true", help="do not start the companion app")
    a = sub.add_parser("app", help="companion chat app")
    a.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7777)))  # Render sets PORT
    a.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))  # 0.0.0.0 on a server
    a.add_argument("--no-open", action="store_true")
    sub.add_parser("serve")
    sub.add_parser("doctor")
    sub.add_parser("skills")
    args = ap.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "skills":
        print(SkillLibrary(cfg.get("agent", {}).get("skills_dir", "skills")).index())
    elif args.cmd == "doctor":
        doctor(cfg)
    elif args.cmd == "serve":
        agent = build_agent(cfg)
        serve_telegram(lambda task, skill: _summary(agent.run(task, skill)))
    elif args.cmd == "app":
        app = _start_app(cfg, port=args.port, open_browser=not args.no_open, host=args.host)
        app.start_worker()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    else:
        _keep_awake()
        app = None if args.no_app else _start_app(cfg, countdown_to=args.at)
        # built before waiting, so config errors show up now, not at 11:00
        agent = build_agent(cfg, bus=app.bus if app else None, inbox=app.inbox if app else None,
                            channels=[app.channel] if app else None)
        if app:
            app.attach(agent)
            app.running = False
        if args.at:
            target = parse_when(args.at)
            if app:
                app.bus.publish("say", text=f"I'm scheduled for {target:%H:%M:%S}. I'll start on my own; "
                                             "you don't need to touch anything.", system=True)
            wait_until(target)
        if args.when_file:
            wait_for_file(args.when_file)
        if args.when_cmd:
            wait_for_command(args.when_cmd)
        if app:
            app.running = True
        try:
            result = agent.run(args.task, args.skill, args.max_steps)
        finally:
            if app:
                app.running = False
            agent.hands.close()
        print(_summary(result))
        if app:  # keep the app up so the final ticket card stays visible
            print("Run finished. The companion app stays open; press Ctrl+C to quit.", flush=True)
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        sys.exit(0 if result.success else 1)


def _keep_awake() -> None:
    """A scheduled run waits a long time; the laptop must not sleep or turn the display off meanwhile."""
    if os.name == "nt":
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    elif sys.platform == "darwin":
        import subprocess

        subprocess.Popen(["caffeinate", "-dimsw", str(os.getpid())])


def _start_app(cfg: dict, port: int = 7777, open_browser: bool = True, countdown_to: str | None = None,
               host: str = "127.0.0.1"):
    from .bus import EventBus
    from .inbox import Inbox
    from .server import App, WebChannel, readiness, serve

    bus, inbox = EventBus(), Inbox()
    runs = Path(cfg.get("agent", {}).get("runs_dir", "runs")).resolve()
    channel = WebChannel(bus, runs)
    target = countdown_to or cfg.get("ui", {}).get("countdown_to", "11:00:00")
    app = App(bus, channel, inbox, lambda: build_agent(cfg, bus=bus, inbox=inbox, channels=[channel]),
              countdown_to=target, checks=lambda: readiness(cfg))
    try:
        serve(app, runs, host=host, port=port, open_browser=open_browser and not os.environ.get("NO_BROWSER"))
    except OSError as e:
        print(f"[!!] companion app could not start on port {port}: {e}. Continuing without it.", flush=True)
    return app


def _summary(result) -> str:
    return (f"{'SUCCESS' if result.success else 'FAILED'}: {result.summary}\n"
            f"steps={result.steps} handoffs={result.handoffs}\nlog: {result.run_dir / 'report.html'}")


def doctor(cfg: dict) -> None:
    print(f"DISPLAY={os.environ.get('DISPLAY')!r}")
    try:
        from .eyes import capture_desktop

        shot = capture_desktop()
        print(f"[ok] screenshot {shot.width}x{shot.height}")
    except Exception as e:
        print(f"[!!] screenshot failed: {e}")
    from .server import readiness

    for c in readiness(cfg):
        print(f"[{'ok' if c['ok'] else '!!'}] {c['name']}" + ("" if c["ok"] else f": {c['fix']}"))
    client = build_model(cfg)
    for p in client.providers:
        if not p.configured:
            print(f"[--] {p.name}: no key ({p.key_env})")
            continue
        try:
            listed = client.http.get(f"{p.base_url}/models", headers={"Authorization": f"Bearer {p.api_key}"})
            ids = [m.get("id", "") for m in listed.json().get("data", [])] if listed.status_code == 200 else []
            t0 = time.monotonic()
            sub = type(client)([p], retries=0)
            reply = sub.complete([{"role": "user", "content": 'Reply with the JSON {"ok": true}'}], max_tokens=200)
            print(f"[ok] {p.name}: {p.model} answered in {time.monotonic() - t0:.1f}s: {reply.text.strip()[:60]}")
        except Exception as e:
            print(f"[!!] {p.name}: {p.model}: {str(e)[:300]}")
            if ids:
                print(f"     models this key can use (first 40): {', '.join(sorted(ids)[:40])}")


if __name__ == "__main__":
    main()
