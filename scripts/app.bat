@echo off
rem Opens the companion chat app. Type any task for Guard there.
cd /d "%~dp0.."
".venv-win\Scripts\python.exe" -m harness app
