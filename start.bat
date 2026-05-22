@echo off
echo ============================================
echo   TikTok Creative Studio
echo ============================================
echo.

cd /d "%~dp0"

:: Determine Python path
if exist "python\python.exe" (
    set PYTHON=python\python.exe
) else (
    set PYTHON=python
)

echo [1] Closing Chrome...
taskkill /F /IM chrome.exe >nul 2>&1
timeout /t 3 >nul

echo [2] Starting Chrome (debug mode)...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%~dp0chrome_profile" --no-first-run
timeout /t 4 >nul

:: Verify port
netstat -ano | findstr "9222" >nul
if %errorlevel%==0 (
    echo     [OK] Chrome debug port ready
) else (
    echo     [!] Port not ready, waiting...
    timeout /t 3 >nul
)

echo [3] Starting server...
echo.
echo     URL: http://localhost:8000
echo     Press Ctrl+C to stop
echo.

start "" http://localhost:8000
%PYTHON% -m uvicorn main:app --host 0.0.0.0 --port 8000 --loop asyncio
