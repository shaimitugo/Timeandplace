@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Please run Install-Windows.bat first.
 pause
 exit /b 1
)
echo Keep this window open while using the app.
echo Once the server starts, open http://localhost:8000 in your browser.
echo Choose Explore the local demo. Press Ctrl+C here to stop.
.venv\Scripts\python.exe -m app.server
pause
