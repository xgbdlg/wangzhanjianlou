@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   CSGOEmpire Sniper - Starting...
echo   API:     http://127.0.0.1:8080/docs
echo   WS:      ws://127.0.0.1:8081
echo ==========================================
echo.

cd backend
pip install -r requirements.txt -q
python main.py
pause
