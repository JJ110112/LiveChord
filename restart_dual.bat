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
taskkill /F /FI "WINDOWTITLE eq LiveChord Personal (8800)*" /FI "IMAGENAME eq cmd.exe" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq LiveChord Beta (8801)*" /FI "IMAGENAME eq cmd.exe" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq LiveChord Server*" /FI "IMAGENAME eq cmd.exe" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Cloudflare Tunnel*" /FI "IMAGENAME eq cmd.exe" >nul 2>&1
REM Fallback sweep — catch any remaining LiveChord-* cmd windows
taskkill /F /FI "WINDOWTITLE eq LiveChord*" /FI "IMAGENAME eq cmd.exe" >nul 2>&1

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
REM Post-beta (2026-04-26 onward) the deployment is single-instance personal
REM on port 8800. The 8801 beta uvicorn is intentionally NOT started anymore;
REM the kill-step above sweeps any leftover. Cloudflare Tunnel still starts
REM here so livechord.org keeps routing — but the tunnel ingress in the CF
REM Zero Trust dashboard must point at http://localhost:8800 (was :8801).
REM Until you update that, livechord.org will 502 after this script runs.

echo Starting LiveChord (8800, personal)...
setlocal
set LIVECHORD_MODE=personal
set LIVECHORD_BTC_WORKERS=2
start "LiveChord Personal (8800)" cmd /c "cd /d %~dp0backend && python -m uvicorn main:app --host 0.0.0.0 --port 8800 --proxy-headers --forwarded-allow-ips=127.0.0.1 || pause"
endlocal

REM Wait for uvicorn to bind before starting tunnel
timeout /t 3 /nobreak >nul

echo Starting Cloudflare Tunnel...
start "Cloudflare Tunnel" cmd /c "cloudflared tunnel run livechord || pause"

echo.
echo ==========================================
echo   Restart complete
echo   - Service: http://localhost:8800 (personal)
echo   - Tunnel:  https://livechord.org
echo   Reminder: CF Tunnel ingress must point at :8800 (was :8801)
echo ==========================================
echo.
echo If the Personal (8800) console did NOT open, scroll up for errors.
echo Press any key to close this window.
pause >nul
