@echo off

echo ==========================================
echo   LiveChord - Dual Mode
echo ==========================================
echo.
echo   Personal : http://localhost:8800  (LAN only)
echo   Beta     : http://localhost:8801  (livechord.org)
echo.

REM === Personal Mode (Port 8800) — 背景視窗 ===
start "LiveChord-Personal" cmd /c "cd /d %~dp0backend && set LIVECHORD_MODE=personal && python run.py"

REM === Beta Mode (Port 8801) — 背景視窗 ===
start "LiveChord-Beta" cmd /c "cd /d %~dp0backend && set LIVECHORD_MODE=beta && python run.py"

echo Both instances started.
echo Close this window or press any key to exit.
pause >nul
