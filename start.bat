@echo off
REM ========================================
REM CSGOEmpire 捡漏助手 — Windows 启动脚本
REM ========================================

cd /d "%~dp0"

echo.
echo ==========================================
echo   CSGOEmpire 双引擎捡漏助手
echo ==========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.11+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] 检查依赖...
pip install -r backend\requirements.txt -q

echo [2/3] 启动后端服务 (http://127.0.0.1:8080)...
echo [3/3] API 文档: http://127.0.0.1:8080/docs
echo.

cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8080

pause
