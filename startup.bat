@echo off
rem Starts the simlab / Sewerbot dashboard and opens the chat page in the browser.
rem   startup.bat                 default: http://127.0.0.1:8000/chat  (the older tabbed app is at http://127.0.0.1:8000/)
rem   startup.bat --no-browser    do not open a browser tab
rem   startup.bat --port 9000     any other `python -m sewerbot` option is passed through
rem Closing this window (or Ctrl+C) stops the dashboard.
setlocal
cd /d "%~dp0"
title simlab dashboard

set "PY=.venv\Scripts\python.exe"
if not defined SIMLAB_PORT set "SIMLAB_PORT=8000"

if not exist "%PY%" (
    echo Python environment not found: %CD%\%PY%
    echo Create it first, see docs\SETUP.md
    echo.
    pause
    exit /b 1
)

rem A second dashboard would start its web server but lose the Webots link (port 8770 is held by the first),
rem so if the port is taken just open the one that is already running.
netstat -an | findstr /R /C:":%SIMLAB_PORT% .*LISTENING" >nul
if not errorlevel 1 (
    echo Port %SIMLAB_PORT% is already in use, probably a dashboard that is already running.
    echo %* | findstr /C:"--no-browser" >nul
    if errorlevel 1 start "" "http://127.0.0.1:%SIMLAB_PORT%/chat"
    "%SystemRoot%\System32\timeout.exe" /t 3 >nul
    exit /b 0
)

echo Starting simlab at http://127.0.0.1:%SIMLAB_PORT%/chat  (Ctrl+C or close this window to stop)
echo.
rem the browser is opened here (on the chat page, once the server is up) instead of by python
echo %* | findstr /C:"--no-browser" >nul
if errorlevel 1 start "" /b cmd /c "ping -n 5 127.0.0.1 >nul & start "" "http://127.0.0.1:%SIMLAB_PORT%/chat""
"%PY%" -m sewerbot --no-browser --port %SIMLAB_PORT% %*
set "CODE=%errorlevel%"

echo.
echo The dashboard stopped (exit code %CODE%).
pause
exit /b %CODE%
