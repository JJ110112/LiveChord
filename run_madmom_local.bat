@echo off
REM ============================================================
REM  Stage step 2/3 — run madmom against local SSD chord JSONs
REM ------------------------------------------------------------
REM  Idempotent — re-run any time to resume. Already-madmom JSONs
REM  are skipped (`beats_source == "madmom"`). Atomic per-file
REM  write means Ctrl+C / shutdown / power loss are safe.
REM
REM  Audio is still resolved through V:\backend\config (music_roots
REM  Y:\, Z:\), but those drives are LOCAL to the PC so there's no
REM  SMB cost on audio reads — just the chord JSONs go via G:\.
REM
REM  Output: G:\stage\livechord\logs\madmom_<TS>.log
REM
REM  Tweakables (edit below):
REM    WORKERS    parallel madmom processes (default 1, ~500MB
REM               RAM each; bump to 2 if PC has plenty headroom)
REM    EXCLUDE    comma-separated case-insensitive substring
REM               blacklist on the chord JSON's `path` field.
REM               Default tries common names — refine after the
REM               first dry-run shows your real folder labels.
REM ============================================================

setlocal
title madmom local (G: stage)

set WORKERS=1
set STAGE_CHORDS=G:\stage\livechord\data\chords
set STAGE_LOG_DIR=G:\stage\livechord\logs
set EXCLUDE=Classics,Sleep

if not exist "%STAGE_CHORDS%" (
    echo ERROR: %STAGE_CHORDS% missing — run stage_madmom_pull.bat first.
    pause & exit /b 1
)
if not exist "%STAGE_LOG_DIR%" mkdir "%STAGE_LOG_DIR%"

REM Run from V:\backend so the script imports config / beat_snap from
REM the canonical runtime — no need to copy backend code locally.
cd /d V:\backend || (echo ERROR: cannot cd to V:\backend & pause & exit /b 1)

for /f %%a in ('python -c "import time;print(time.strftime('%%Y%%m%%d_%%H%%M%%S'))"') do set TS=%%a
set LOG=%STAGE_LOG_DIR%\madmom_%TS%.log

echo === madmom local batch starting ===
echo Chord dir: %STAGE_CHORDS%
echo Log:       %LOG%
echo Workers:   %WORKERS%   (priority: BELOWNORMAL)
echo Filter:    --exclude "%EXCLUDE%"
echo.
echo Idempotent — Ctrl+C / shutdown anytime; re-run this .bat to resume.
echo (Run a dry-run first if you want to verify the filter:
echo   python migrate_add_dynamic_beats.py --dry-run --limit 20 --chords-dir "%STAGE_CHORDS%" --exclude "%EXCLUDE%")
echo.

start /BELOWNORMAL /B /WAIT "" powershell -NoProfile -Command ^
  "python -u migrate_add_dynamic_beats.py --workers %WORKERS% --exclude '%EXCLUDE%' --chords-dir '%STAGE_CHORDS%' 2>&1 | Tee-Object -FilePath '%LOG%'"

echo.
echo === finished (exit code %errorlevel%) ===
echo Log saved: %LOG%
echo Next step (when fully done): stage_madmom_push.bat
pause
endlocal
