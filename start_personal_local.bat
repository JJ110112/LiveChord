@echo off
REM =====================================================================
REM  LiveChord - Local Personal Test Instance (PC, port 8803)
REM
REM  Runs the dev-repo backend in personal mode on port 8803 so we can QA
REM  personal-only features (auto-worker, admin UI, /api/extraction/*,
REM  /api/auto/*, /api/library/*, /api/settings, LAN-bypass auth, etc.)
REM  without touching the NUC runtime at V:\ (port 8800 in prod).
REM
REM  This script is safe for AI agents to launch in the background:
REM  - Binds 127.0.0.1 only (no LAN exposure, no collision with NUC 8800).
REM  - No interactive pause — uvicorn stays in the foreground; the caller
REM    kills it with Ctrl+C or process termination when QA is done.
REM
REM  Data folder is the dev repo's .\data\ (NOT V:\data\). Personal + beta
REM  locals share this folder; settings are split via settings_personal.json
REM  vs settings_beta.json per the dual-instance isolation design.
REM
REM  LAN-bypass auth returns "admin" for 127.0.0.1, so the admin page
REM  opens without login — matches the 8800 prod behaviour on LAN.
REM =====================================================================

setlocal
set LIVECHORD_MODE=personal
set LIVECHORD_BTC_WORKERS=1

cd /d "%~dp0backend"

echo ==========================================
echo   LiveChord Personal Local (Port 8803)
echo ==========================================
echo.
echo   URL:    http://localhost:8803/
echo   Admin:  http://localhost:8803/admin
echo   Mode:   personal (LAN bypass, full NAS, no login on 127.0.0.1)
echo   Data:   %~dp0data  (dev repo, NOT V:\data)
echo.
echo   Ctrl+C to stop.
echo ==========================================
echo.

python -m uvicorn main:app --host 127.0.0.1 --port 8803 --reload
