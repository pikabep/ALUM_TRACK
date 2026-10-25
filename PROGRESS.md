# Progress (25 Oct 2026)

Done
- Harness core: hands, eyes, loop, model fallback chain, run log with replay (`report.html`), skills with learned notes.
- Windows support: UTF-8 file IO, terminal handoff without `select`, sleep inhibitor, `.venv-win`.
- Payment gate in code, plus tests for every route to a pay click (`tests/test_gate.py`).
- One-booking lock, success check on the PNR, passenger file in every prompt.
- OTP inbox (phone webhook) with the human as fallback.
- Companion chat app: narration, live screenshot, patience bar, question card, boarding-pass approval card,
  ticket card with handoff count, countdown with readiness checks.
- IRCTC skill `skills/irctc_tatkal/SKILL.md`; `scripts/book_tatkal.bat`.
- Verified: 22 tests pass on Windows; real run on Chrome with Gemini succeeded (example.com task).

Before 09:00 (human)
- Fill `.env` (IRCTC_USERNAME, IRCTC_PASSWORD; a second model key), `data/passenger.txt`.
- Run `python -m harness doctor`; start `scripts/book_tatkal.bat`; start the screen recording and the phone video.
