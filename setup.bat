@echo off
echo ============================================
echo   TikTok Creative Studio - First Time Setup
echo ============================================
echo.

cd /d "%~dp0"

:: Check if embedded Python exists
if exist "python\python.exe" (
    echo [OK] Python runtime found
) else (
    echo [1/4] Downloading embedded Python...
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip' -OutFile 'python_embed.zip'"
    if not exist "python_embed.zip" (
        echo [ERROR] Download failed. Check your network connection.
        pause
        exit /b 1
    )
    echo [2/4] Extracting Python...
    powershell -Command "Expand-Archive -Path 'python_embed.zip' -DestinationPath 'python' -Force"
    del python_embed.zip

    :: Enable pip in embedded Python
    echo [3/4] Setting up pip...
    echo import site>> "python\python312._pth"
    powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'python\get-pip.py'"
    "python\python.exe" "python\get-pip.py" --no-warn-script-location
    del "python\get-pip.py"
)

echo.
echo [Install] Installing Python packages...
"python\python.exe" -m pip install -r requirements.txt --no-warn-script-location -q

echo.
echo [Install] Installing Playwright Chromium...
"python\python.exe" -m playwright install chromium

echo.
echo ============================================
echo   Setup complete! Run start.bat to launch.
echo ============================================
pause
