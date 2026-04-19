@echo off
REM =====================================================================
REM  LiveChord - Local Beta Test Instance (PC, port 8802)
REM
REM  Runs the dev-repo backend in beta mode on port 8802 so we can QA
REM  beta-only features (YT search, candidate picker, etc.) without
REM  touching the NUC runtime at V:\ (port 8801 in prod).
REM
REM  Uses the dev repo's local data\ folder — NUC's data\ (V:\data\) is
REM  untouched.  Auth DB is whatever sits in .\data\auth.db (separate
REM  from prod users).
REM =====================================================================

setlocal
set LIVECHORD_MODE=beta
set LIVECHORD_BTC_WORKERS=1

cd /d "%~dp0backend"

echo ==========================================
echo   LiveChord Beta Local (Port 8802)
echo ==========================================
echo.
echo   URL:  http://localhost:8802/
echo   Mode: beta (forced login, hashed NAS paths)
echo   Data: %~dp0data  (dev repo, NOT V:\data)
echo.
echo   Ctrl+C to stop.
echo ==========================================
echo.

python -m uvicorn main:app --host 127.0.0.1 --port 8802 --reload
pause
