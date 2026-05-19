@echo off
chcp 65001 >nul
echo ============================================
echo   TikTok Creative Studio 自动化 - 首次安装
echo ============================================
echo.

cd /d "%~dp0"

:: Check if embedded Python exists
if exist "python\python.exe" (
    echo [OK] 已检测到 Python 运行时
) else (
    echo [1/4] 下载嵌入式 Python...
    echo       请稍候，正在下载约 20MB...
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip' -OutFile 'python_embed.zip'"
    echo [2/4] 解压 Python...
    powershell -Command "Expand-Archive -Path 'python_embed.zip' -DestinationPath 'python' -Force"
    del python_embed.zip

    :: Enable pip in embedded Python
    echo [3/4] 配置 pip...
    echo import site>> python\python312._pth
    powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'python\get-pip.py'"
    python\python.exe python\get-pip.py --no-warn-script-location
    del python\get-pip.py
)

echo.
echo [安装依赖] 安装 Python 包...
python\python.exe -m pip install -r requirements.txt --no-warn-script-location -q

echo.
echo [安装浏览器] 安装 Playwright Chromium...
python\python.exe -m playwright install chromium

echo.
echo ============================================
echo   安装完成！请运行 start.bat 启动程序
echo ============================================
pause
