@echo off
rem Start this BEFORE the 09:00 freeze and leave the window open. It waits on its own,
rem keeps the laptop awake, logs in at 10:52, waits for 11:00:00 and books.
rem The companion app opens in your browser: approve the payment there (or on Telegram).
cd /d "%~dp0.."
w32tm /resync >nul 2>&1
".venv-win\Scripts\python.exe" -m harness run "Book the cheapest Tatkal ticket from Roorkee to Haridwar for tomorrow for the passenger in data/passenger.txt" --skill irctc_tatkal --at 10:52:00
pause
