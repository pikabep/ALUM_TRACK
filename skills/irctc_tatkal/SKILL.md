---
name: irctc_tatkal
description: Book the cheapest Tatkal train ticket on IRCTC (irctc.co.in) for one passenger, e.g. Roorkee to Haridwar tomorrow
secrets: ["IRCTC_USERNAME", "IRCTC_PASSWORD"]
context_files: ["data/passenger.txt"]
approve_before: ["Pay & Book", "Make Payment", "Pay Now", "Proceed to Pay"]
done_requires: ["\\bPNR\\b[^\\d]{0,40}\\d{10}"]
once: true
max_steps: 160
---
# Purpose
Book ONE confirmed Tatkal ticket, the cheapest one that is actually bookable, for the passenger in
`data/passenger.txt` (shown above), on the IRCTC website, then save proof. The operator only approves the
payment. Every other handoff costs points, so solve problems yourself first.

# Facts (re-check on screen, do not trust blindly)
- Site: https://www.irctc.co.in/nget/train-search . It is an Angular app: slow, with "Please wait..." loaders.
- Tatkal opens ONE DAY BEFORE the journey: 10:00 for AC classes (1A 2A 3A 3E CC EC), 11:00 for non-AC (SL, 2S).
  Journey date = TOMORROW relative to "Now" above. Write it as DD/MM/YYYY.
- Stations: Roorkee = `ROORKEE - RK`, Haridwar = `HARIDWAR JN - HW`. Always pick from the autosuggest list.
- On this short route the cheapest Tatkal is usually Second Sitting (2S), sometimes Sleeper (SL). Still compare
  every class shown; never assume.

# Timeline
1. If it is before 10:58, do steps A and B (login + fill the search form) now, then
   `wait_until("10:59:59")`. If the session expired while waiting (a LOGIN button is back), log in again fast.
2. At/after 11:00:00 click Search (step C). If Tatkal classes are not open yet ("not available for booking",
   no Tatkal fare), wait 2-3 s and click Search again; never more than once per 3 s.

# A. Login (ALWAYS FIRST, before touching the search form)
IRCTC re-opens the LOGIN popup by itself whenever you touch the search form while logged out. Do NOT close the
login popup and go back to the form: that loops forever. Log in first.
1. `browser_goto` https://www.irctc.co.in/nget/train-search . A language popup may ask for a language: click
   "English". Close other popups/alerts ("OK", "×", Disha chatbot, notification prompts) by their close button.
2. Already logged in? The header shows the user name / "Welcome" and a LOGOUT link: skip to B.
3. Click LOGIN (top right). A login panel opens with "User Name", "Password", a captcha image and
   "Enter Captcha".
4. Type `{{secret:IRCTC_USERNAME}}` into User Name and `{{secret:IRCTC_PASSWORD}}` into Password.
5. Captcha: read the characters from the screenshot yourself (look closely: case matters, no spaces; confuse-pairs
   are 0/O, 1/l/I, 5/S, 8/B). Use look="browser" for this step. Type it, click SIGN IN.
6. "Invalid Captcha": a new image appears. Read again (max 3 tries, 2 s apart). After 3 failures ask_human
   with the screenshot ("Please read the captcha for me").
7. If an OTP is requested at login: call `inbox_wait_for_code(timeout=60, sender="IRCTC|UIDAI|CRIS")`, type it
   immediately, submit. No code after 60 s: click "Resend OTP" once and wait again; still nothing: ask_human for it.
8. "Invalid user name or password" / account locked / "Aadhaar not authenticated": ask_human right away
   (cannot be fixed by retrying).
Success: the header shows the account name and LOGOUT.

# B. Fill the search form (on the train-search page, "BOOK TICKET" box)
1. From: type `ROORKEE` key by key into the From input (browser_type with delay_ms=150, clear=true). In the
   NEXT step, with a fresh observation, click the suggestion item reading `ROORKEE - RK` (a list item under the
   input). If no list shows, press ArrowDown then Enter on the input. Verify the input now says ROORKEE - RK.
2. To: same with `HARIDWAR`, click `HARIDWAR JN - HW`.
3. Date: click the date input, select all (Control+A), type tomorrow as DD/MM/YYYY, press Escape to close
   the calendar. Verify the input shows tomorrow's date. If typing does not stick, use the calendar
   (arrow to next month if needed, click the day).
4. Quota dropdown (shows GENERAL): click it, choose `TATKAL`. Verify it shows TATKAL.
5. Class: leave "All Classes".
6. Do NOT click Search before 11:00:00 for non-AC.

# C. Search and find the cheapest (the cheapest rule)
1. Click "Search". Wait patiently for the train list (loaders can take 30-60 s right after 11:00).
   An "Alert"/"Confirmation" popup about Tatkal timings or "disclaimer": accept with OK/Yes and continue.
2. The list shows train cards (number, name, departure time) with class boxes like "Second Sitting (2S)",
   "Sleeper (SL)", "AC Chair car (CC)". Some lists need scrolling: scroll until you have seen every train.
3. For each train and each class: click the class box (or "Refresh"/its date cell) to load availability.
   It shows the date with a status like `AVAILABLE-0023`, `AVL 12`, `RAC 5`, `WL 14`, `REGRET`,
   `NOT AVAILABLE`, `TRAIN DEPARTED`, and the fare (₹).
4. Eligible = status shows AVAILABLE / AVL with seats. NOT eligible: RAC, WL (any waitlist), REGRET,
   NOT AVAILABLE, CHART PREPARED, TRAIN DEPARTED, "booking not allowed".
5. Write the table as you go with `file_write` to `<Run folder>/fare_table.json`:
   `[{"train": "14317 ...", "dep": "06:05", "class": "2S", "status": "AVAILABLE-0040", "fare": 45}, ...]`.
6. Choose the eligible option with the LOWEST fare. Tie: earliest departure. Save your choice with a `note`
   only if it teaches a lasting lesson; otherwise just state it in "say", e.g. "Cheapest is 2S on 14317 at ₹45.
   Checked 6 trains, 9 classes."
7. With that train's class + date selected, click "Book Now" ONCE. Then wait.
8. If it says sold out / waitlisted / regret at this point, go back to the list and take the next cheapest
   eligible option. No handoff for this.

# D. Passenger details (page title "Passenger Details", URL has psgninput)
1. If a login prompt appears, log in (step A) and continue.
2. Fill exactly from data/passenger.txt: Name (as on ID), Age, Gender, Nationality (India), Berth Preference
   (No Preference if not given). If a "saved passenger" dropdown offers the same person, you may pick it, but
   then verify every field.
3. Re-read every field on screen and compare with the file. Fix any mismatch before continuing.
4. Checkboxes: tick "Book only if confirm berths are allotted" (if shown). Leave "Consider for Auto
   Upgradation" unticked unless the file says otherwise. Mobile number: keep the prefilled one.
5. Payment mode on this page (if asked): choose the one in the file (default "BHIM/UPI" / "Pay through BHIM/UPI").
6. Captcha on this page (if any): read it like in A.5.
7. Click "Continue" ONCE. Wait for the next page (can be slow). Never click Continue again while a loader shows.

# E. Aadhaar OTP / review
1. If an OTP box appears ("OTP sent to your registered mobile / Aadhaar linked mobile"): call
   `inbox_wait_for_code(timeout=60, sender="IRCTC|UIDAI|CRIS")`, type the code at once, submit. Resend once if
   nothing arrives, then ask_human for the OTP.
2. Review Journey page: check train, date (tomorrow), From RK, To HW, class, quota TATKAL, passenger name/age/
   gender. Enter its captcha if shown and click "Continue".

# F. Payment (the harness gate asks the operator; you never pay on your own)
1. On the payment page choose the method from the file (default: BHIM/UPI, e.g. "Pay through BHIM/UPI",
   or IRCTC eWallet if the file says irctc_wallet).
2. Click "Pay & Book" (or "Make Payment"). The harness pauses and asks the operator with a summary taken from
   the page. Wait for the answer. If NOT approved: do not retry; call done(success=false, summary="payment
   not approved").
3. On a UPI gateway page: if it asks for a UPI ID / VPA, type the one from the file and click the gateway's
   Pay/Verify button. Then the operator approves the collect request in their UPI app on the phone.
   NEVER type a UPI PIN, card number, CVV or bank password.
4. After the pay click: ONLY wait and observe (browser_wait with timeout 120, several times if needed).
   NEVER reload, go back or click Pay again: a double payment is the worst outcome.
5. If unclear after ~5 minutes: open My Account > My Transactions / Booked Ticket History in a NEW tab and check
   whether the ticket was booked before doing anything else.

# G. Proof (success checks)
The booking confirmation page shows "PNR" with a 10-digit number and "Booked"/"CNF" status.
1. `browser_save_screenshot(path="<Run folder>/ticket_with_PNR.png", full_page=true)`.
2. If there is a "Download"/"Print ERS" button, click it (downloads go to the runs folder automatically).
3. `file_write` `<Run folder>/summary.json` with PNR, train, class, date, passenger, fare, booked_at.
4. `done(summary="Booked PNR <number>: <train>, <class>, <date>, ₹<fare>, <passenger>")`. The harness refuses
   success unless a PNR is visible on screen.

# Common problems and recovery (patience first)
- "Please wait...", spinner, greyed buttons: slow, not broken. browser_wait for the loader to disappear
  (timeout 60-90). Do not click again.
- Blank page / same screen for 20+ s: scroll a little, wait 10 s more, then browser_reload (only BEFORE the
  passenger form was submitted; at most once per 10 s, 5 times per stage).
- "Server busy", "Service Unavailable", "Something went wrong", 502/503: wait 5-8 s, then repeat the last safe step
  (search is safe; Book Now/Continue/Pay are not: first check whether the last click already worked).
- "Session expired" / logged out: log in again (A) and resume from the stage you were in.
- Popups (Alert, Disha, ads, cookie notices): close via their OK/close button, then re-observe.
- Element covered ("intercepts pointer events"): a loader is on top; wait for it to go away.
- Autosuggest not appearing: clear the field and type more slowly (delay_ms=200).

# Hand off to a human only when
- Payment approval (automatic, by the harness).
- Captcha failed 3 times; OTP not received after one resend; wrong password / locked / Aadhaar not authenticated.
- The recovery steps above are exhausted for the current stage.
