@echo off

REM Post-beta deployment mode pin (see start.bat for rationale). The `set`
REM here propagates into the `start` child process below.
set LIVECHORD_MODE=personal

REM 確保防火牆允許 port 8800
netsh advfirewall firewall show rule name="LiveChord Server" >nul 2>&1
if %errorlevel% neq 0 (
    echo Adding firewall rule for port 8800...
    netsh advfirewall firewall add rule name="LiveChord Server" dir=in action=allow protocol=TCP localport=8800 >nul 2>&1
)

echo ==========================================
echo   LiveChord - Restarting...
echo ==========================================

REM 找到並關閉舊的 uvicorn 程序。同時掃 8801 / 8802 — 這些是 2026-04 dual-instance
REM 留下的 beta/8802 leftover；post-beta 期間應該都不在跑，留著保險。
for %%P in (8800 8801 8802) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P.*LISTENING"') do (
        echo Stopping PID %%a on port %%P ...
        taskkill /F /PID %%a >nul 2>&1
    )
)
timeout /t 2 /nobreak >nul

REM 啟動新的服務
cd /d "%~dp0backend"
echo Starting server on http://localhost:8800 ...
start "LiveChord Server" python -m uvicorn main:app --host 0.0.0.0 --port 8800
echo ==========================================
echo   LiveChord is running!
echo   http://localhost:8800
echo ==========================================
