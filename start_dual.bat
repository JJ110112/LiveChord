@echo off
echo Starting LiveChord Dual Mode...

REM Start Personal Mode (Port 8800)
set LIVECHORD_MODE=personal
start "LiveChord Personal (8800)" cmd /c "cd /d "%~dp0backend" && python -m uvicorn main:app --host 0.0.0.0 --port 8800 --reload & pause"

REM Start Beta Mode (Port 8801)
set LIVECHORD_MODE=beta
start "LiveChord Beta (8801)" cmd /c "cd /d "%~dp0backend" && python -m uvicorn main:app --host 0.0.0.0 --port 8801 & pause"

echo Both modes started in new windows.
echo - Personal Mode: http://localhost:8800  (LAN Bypass enabled)
echo - Beta Mode: http://localhost:8801  (Ready for Cloudflare Tunnel)
