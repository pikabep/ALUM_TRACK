# Decisions

- **Free OpenAI-compatible model chain (Gemini, then Groq, then OpenRouter)** instead of the plan's Anthropic default:
  these are the keys the team has. A rate-limited provider hands the step to the next.
- **Companion server uses only the standard library** (ThreadingHTTPServer plus server-sent events) instead of
  FastAPI/React. There is nothing extra to install or build, so less can break on the day. The UI is one static
  file, `ui/index.html`.
- **The payment gate is generic code, not IRCTC code**: URL patterns (pay, checkout, gateway, upi...) plus pay-button
  text, checked for every route to a click (ref, selector, text, x/y via elementFromPoint, focused element plus
  Enter, JavaScript). "Pay through ..." payment-mode radios are excluded so approval is asked once, at the real
  payment step. One approval covers 6 gated actions within 180 s (IRCTC "Pay & Book", then the gateway's Pay).
- **Generic skill features instead of site code**: `once` (lock after success), `done_requires` (success refused
  unless the proof is visible on screen), `context_files` (the passenger file goes into every prompt),
  `approve_before`.
- **Installed Chrome (`channel = "chrome"`)**: looks like a normal browser to bot checks, and needs no download.
- **`--at` that has just passed runs now** (within 2 h) instead of waiting until tomorrow.
- **The run starts at 10:52** so login and the captcha finish before 11:00. The skill waits until 10:59:59 and
  logs in again if the session expired.
- **Not done (time)**: NTP clock (we use `w32tm /resync`), ffmpeg recorder (use Win+Alt+R Xbox Game Bar or OBS),
  APScheduler and OS backup jobs (two processes could fight over the browser profile), mock IRCTC site.
