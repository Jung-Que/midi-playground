@echo off
rem Live mode: settings in live.json (category, video_id, texts). Close with ESC.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py --live %*
if errorlevel 1 pause
