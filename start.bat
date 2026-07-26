@echo off
cd /d "%~dp0启动程序"
start http://127.0.0.1:8080/docs
echo ===================================
echo   CSGOEmpire Sniper
echo   http://127.0.0.1:8080/docs
echo   Close this window to stop.
echo ===================================
echo.
CSGOEmpireSniper.exe
pause
