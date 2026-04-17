@echo off
echo ==========================================
echo   LiveChord - Restarting Dual Mode...
echo ==========================================

REM === Stop all services ===

echo [1/4] Stopping Cloudflare Tunnel...
taskkill /F /IM cloudflared.exe >nul 2>&1
if %errorlevel% equ 0 (
    echo   Cloudflare Tunnel stopped.
) else (
    echo   Cloudflare Tunnel was not running.
)

echo [2/4] Stopping LiveChord services by window title...
taskkill /F /FI "WINDOWTITLE eq LiveChord Personal (8800)" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq LiveChord Beta (8801)" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq LiveChord Server" >nul 2>&1

echo [3/4] Killing any process on port 8800 and 8801...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R "LISTENING" ^| findstr /R ":8800[^0-9]"') do (
    echo   Killing PID %%a on port 8800...
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R "LISTENING" ^| findstr /R ":8801[^0-9]"') do (
    echo   Killing PID %%a on port 8801...
    taskkill /F /PID %%a >nul 2>&1
)

echo [4/4] Waiting for ports to release...
timeout /t 3 /nobreak >nul

REM Verify ports are free
netstat -ano | findstr /R "LISTENING" | findstr /R ":8800[^0-9]" >nul 2>&1
if %errorlevel% equ 0 (
    echo   WARNING: Port 8800 still in use, retrying...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R "LISTENING" ^| findstr /R ":8800[^0-9]"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 2 /nobreak >nul
)
netstat -ano | findstr /R "LISTENING" | findstr /R ":8801[^0-9]" >nul 2>&1
if %errorlevel% equ 0 (
    echo   WARNING: Port 8801 still in use, retrying...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R "LISTENING" ^| findstr /R ":8801[^0-9]"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 2 /nobreak >nul
)

REM === Firewall rules ===
netsh advfirewall firewall show rule name="LiveChord Server" >nul 2>&1
if %errorlevel% neq 0 (
    echo Adding firewall rule for port 8800...
    netsh advfirewall firewall add rule name="LiveChord Server" dir=in action=allow protocol=TCP localport=8800 >nul 2>&1
)
netsh advfirewall firewall show rule name="LiveChord Beta" >nul 2>&1
if %errorlevel% neq 0 (
    echo Adding firewall rule for port 8801...
    netsh advfirewall firewall add rule name="LiveChord Beta" dir=in action=allow protocol=TCP localport=8801 >nul 2>&1
)

REM === Start services ===

echo.
echo Starting LiveChord Personal (8800)...
setlocal
set LIVECHORD_MODE=personal
start "LiveChord Personal (8800)" cmd /c "cd /d %~dp0backend && python -m uvicorn main:app --host 0.0.0.0 --port 8800 & pause"
endlocal

echo Starting LiveChord Beta (8801)...
setlocal
set LIVECHORD_MODE=beta
start "LiveChord Beta (8801)" cmd /c "cd /d %~dp0backend && python -m uvicorn main:app --host 0.0.0.0 --port 8801 & pause"
endlocal

REM Wait for uvicorn to bind before starting tunnel
timeout /t 3 /nobreak >nul

echo Starting Cloudflare Tunnel...
start "Cloudflare Tunnel" cmd /c "cloudflared tunnel run livechord & pause"

echo.
echo ==========================================
echo   All services restarted!
echo   - Personal: http://localhost:8800
echo   - Beta:     http://localhost:8801
echo   - Tunnel:   https://livechord.org
echo ==========================================
