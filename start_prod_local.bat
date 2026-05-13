@echo off
REM =====================================================================
REM  LiveChord - Local Product Review Instance (public mode, port 8803)
REM
REM  Runs this checkout as the public product experience so homepage,
REM  logged-out demo flow, guest upload, i18n, SEO copy, and player UX can
REM  be reviewed locally without touching beta beat-testing ports or prod.
REM
REM  Uses the dev repo's local data\ folder. This is not the NUC runtime.
REM =====================================================================

setlocal
set LIVECHORD_MODE=public
set LIVECHORD_BTC_WORKERS=1

cd /d "%~dp0backend"

echo ==========================================
echo   LiveChord Product Local (Port 8803)
echo ==========================================
echo.
echo   URL:  http://localhost:8803/
echo   Mode: public product review
echo   Data: %~dp0data  (dev repo, NOT V:\data)
echo.
echo   Use this for homepage/demo/guest-flow review.
echo   Ctrl+C to stop.
echo ==========================================
echo.

python -m uvicorn main:app --host 127.0.0.1 --port 8803 --reload
pause
