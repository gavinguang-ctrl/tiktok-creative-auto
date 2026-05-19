@echo off
chcp 65001 >nul
echo ============================================
echo   TikTok Creative Studio 自动化
echo ============================================
echo.

cd /d "%~dp0"

:: Determine Python path
if exist "python\python.exe" (
    set PYTHON=python\python.exe
) else (
    set PYTHON=python
)

echo [1] 关闭所有 Chrome 进程...
taskkill /F /IM chrome.exe >nul 2>&1
timeout /t 3 >nul

echo [2] 启动 Chrome (调试模式)...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%~dp0chrome_profile" --no-first-run
timeout /t 4 >nul

:: Verify port
netstat -ano | findstr "9222" >nul
if %errorlevel%==0 (
    echo     [OK] Chrome 调试端口就绪
) else (
    echo     [!] 端口未就绪，等待中...
    timeout /t 3 >nul
)

echo [3] 启动服务...
echo.
echo     访问地址: http://localhost:8000
echo     按 Ctrl+C 停止服务
echo.

start "" http://localhost:8000
%PYTHON% -m uvicorn main:app --host 0.0.0.0 --port 8000
