# Skilled Hands

One generic computer-use agent ("Guard") plus a folder of skills. It sees the screen (screenshots plus page DOM),
decides, acts through a shell, files, a real Chrome and the OS mouse and keyboard, checks the result, and loops.
Booking a Tatkal ticket is one skill (`skills/irctc_tatkal/SKILL.md`), not code.

| Part | Where |
|---|---|
| Hands | `harness/hands/` shell, files, browser (Playwright on your installed Chrome), desktop (PyAutoGUI) |
| Eyes | `harness/eyes/` screenshots with numbered element boxes, DOM snapshot, terminal output |
| Loop | `harness/loop.py` plan, act, observe, verify, repeat; stuck detection; patience rules |
| Skills | `skills/*/SKILL.md`; lessons noted during a run are appended under "Learned from runs" |
| Triggers | `run --at 10:52:00`, `--when-file`, `--when-cmd`, Telegram `/run`, the chat app |
| Handoff | chat app first, terminal and Telegram mirror it; first answer wins |
| Payment gate | `Agent._guard` in `harness/loop.py`: any pay-like click, Enter, script or coordinate click, or anything on a payment URL, needs a human yes |
| Run log | `runs/<id>/steps.jsonl`, `shots/`, `report.html` (replay), `meta.json` (handoff count) |

## Setup (Windows)
```
python -m venv .venv-win
.venv-win\Scripts\python -m pip install -r requirements.txt
copy data\passenger.example.txt data\passenger.txt   (then fill it in)
```
`.env`: `GEMINI_API_KEY` (add `GROQ_API_KEY` / `OPENROUTER_API_KEY` as fallbacks), `IRCTC_USERNAME`,
`IRCTC_PASSWORD`, optional `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, optional `INBOX_SECRET`.

Check everything: `.venv-win\Scripts\python -m harness doctor`

## The booking
Run `scripts\book_tatkal.bat` before the freeze and leave it open. It keeps the laptop awake, opens the chat app,
starts at 10:52, logs in, fills the search, waits for 11:00:00, picks the cheapest available Tatkal class,
fills the passenger and asks you once, on the payment card, before paying. Proof is saved to `runs/<id>/`.
`state/irctc_tatkal.done.lock` makes a second run a no-op after a successful booking.

## OTP from the phone
Set `INBOX_SECRET` in `.env`. In an Android SMS-forwarder app, forward SMS from IRCTC/UIDAI as a webhook POST to
`http://<laptop IP on your hotspot>:7778/inbox` with JSON `{"secret": "<INBOX_SECRET>", "from": "%from%", "text": "%text%"}`.
Allow Python through the Windows firewall when asked. Without it, Guard asks you for the OTP in the chat.

## Any other task
`scripts\app.bat` opens the chat. Type a task, e.g. "make a folder on the Desktop called notes and save today's
date in it". Or: `python -m harness run "..."`.

## Tests
`.venv-win\Scripts\python -m pytest -q tests/test_loop.py tests/test_gate.py tests/test_server.py`
