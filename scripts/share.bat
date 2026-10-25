@echo off
rem Public demo link for the companion app, no account needed (Cloudflare quick tunnel).
rem Uses its own port (7780) and its own browser profile, so it never collides with the
rem 11:00 booking (scripts\book_tatkal.bat uses port 7777 and the main profile).
rem Share https://<random>.trycloudflare.com/#<token> -- the token is printed below.
cd /d "%~dp0.."
if "%APP_TOKEN%"=="" for /f %%t in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString('N')"') do set APP_TOKEN=%%t
set HARNESS_PROFILE=%USERPROFILE%\.harness\demo-profile
set NO_BROWSER=1
start "Skilled Hands demo app" ".venv-win\Scripts\python.exe" -m harness app --port 7780 --no-open
echo.
echo Token: %APP_TOKEN%
echo Open the trycloudflare.com link below and add  #%APP_TOKEN%  to the end of it.
echo.
cloudflared tunnel --url http://127.0.0.1:7780
