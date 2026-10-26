@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_CMD=py -3"
where py >nul 2>nul
if errorlevel 1 set "PYTHON_CMD=python"
%PYTHON_CMD% -c "import sys; assert sys.version_info >= (3,12), 'Please install Python 3.12 or newer.'"
if errorlevel 1 goto failed
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Installation complete. Double-click Start-Windows.bat next.
pause
exit /b 0
:failed
echo.
echo Installation failed. Install Python from python.org, then try again.
echo If Python is installed, keep this error message to help troubleshoot.
pause
exit /b 1
