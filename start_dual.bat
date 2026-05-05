@echo off
REM Post-beta (2026-04-26 onward) the live deployment is single-instance
REM personal on port 8800. The dual-instance "Personal+Beta" flow is
REM archival; this script keeps its filename for muscle memory but only
REM starts 8800 personal + Cloudflare Tunnel. To also kill any leftover
REM 8801/8802 process from before, use restart_dual.bat instead.
echo Starting LiveChord (post-beta single-instance)...

setlocal
set LIVECHORD_MODE=public
set LIVECHORD_ADMIN_EMAILS=hiteacherwu@gmail.com
set LIVECHORD_BTC_WORKERS=2
start "LiveChord Personal (8800)" cmd /c "cd /d %~dp0backend && python -m uvicorn main:app --host 0.0.0.0 --port 8800 --proxy-headers --forwarded-allow-ips=127.0.0.1 || pause"
endlocal

timeout /t 3 /nobreak >nul

echo Starting Cloudflare Tunnel...
start "Cloudflare Tunnel" cmd /c "cloudflared tunnel run livechord || pause"

echo.
echo - Service: http://localhost:8800 (personal)
echo - Tunnel:  https://livechord.org
echo Reminder: CF Tunnel ingress must point at :8800 (was :8801 in beta).
