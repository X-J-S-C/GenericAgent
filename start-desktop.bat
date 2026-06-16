@echo off
REM ============================================================================
REM   GenericAgent Desktop — Windows 启动脚本
REM   用法: start-desktop.bat
REM ============================================================================

chcp 65001 > nul
cd /d "%~dp0"

echo =========================================
echo   GenericAgent Desktop 启动 (Windows)
echo =========================================
echo.

REM ─── 检查 Python 依赖 ─────────────────────────────────────────────────────
echo [1/3] 检查 Python 依赖...
python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo   正在安装 fastapi / uvicorn / requests ...
    python -m pip install fastapi uvicorn requests
)
echo   OK.

REM ─── 检查 mykey.py ───────────────────────────────────────────────────────
echo.
echo [2/3] 检查配置文件...
if not exist "mykey.py" (
    echo   mykey.py 不存在，自动创建默认配置 (deepseek-v4-pro)
    (
        echo native_oai_config = ^{
        echo     "name": "deepseek-v4-pro",
        echo     "apikey": "sk-041685ca0cc640adaaa3669fa0f88b80",
        echo     "apibase": "https://api.deepseek.com/v1",
        echo     "model": "deepseek-v4-pro",
        echo     "api_mode": "chat_completions",
        echo     "max_retries": 3,
        echo     "connect_timeout": 10,
        echo     "read_timeout": 120,
        echo     "max_tokens": 8192,
        echo ^}
    ) > "mykey.py"
)
echo   OK.

REM ─── 启动 Electron ────────────────────────────────────────────────────────
echo.
echo [3/3] 启动桌面端...
cd desktop

if not exist "node_modules" (
    echo   首次运行，正在安装 Electron 依赖 (npm install) ...
    call npm install
)

echo   启动中...
call npm start
