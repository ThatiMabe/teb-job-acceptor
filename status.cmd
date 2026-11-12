@echo off
rem Double-click: sends a status message and a screenshot of each site to your Telegram.
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0daily_report.py" --now
echo Sent to Telegram.
timeout /t 3 >nul
