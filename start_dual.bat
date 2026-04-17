@echo off
echo Starting LiveChord Dual Mode...

REM === Personal Mode (Port 8800) ===
setlocal
set LIVECHORD_MODE=personal
set LIVECHORD_BTC_WORKERS=2
start "LiveChord Personal (8800)" cmd /c "cd /d %~dp0backend && python -m uvicorn main:app --host 0.0.0.0 --port 8800 || pause"
endlocal

REM === Beta Mode (Port 8801) ===
setlocal
set LIVECHORD_MODE=beta
set LIVECHORD_BTC_WORKERS=2
start "LiveChord Beta (8801)" cmd /c "cd /d %~dp0backend && python -m uvicorn main:app --host 0.0.0.0 --port 8801 || pause"
endlocal

echo Both modes started.
echo - Personal: http://localhost:8800  (LAN bypass)
echo - Beta:     http://localhost:8801  (Cloudflare Tunnel)
