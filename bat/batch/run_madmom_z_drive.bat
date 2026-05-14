@echo off
REM ============================================================
REM  madmom batch: Z:\ music library, single low-priority worker
REM ------------------------------------------------------------
REM  Idempotent — script skips songs already on madmom beats.
REM  Safe to Ctrl+C / shutdown / reboot at any time. Re-running
REM  this batch picks up where it stopped.
REM
REM  Output: V:\backend\logs\madmom_z_<timestamp>.log
REM
REM  Tweakables:
REM    WORKERS    parallel madmom processes (default 1, ~500MB
REM               RAM each; bump to 2 if PC has plenty headroom)
REM    EXCLUDE    comma-separated substring blacklist
REM    ONLY       substring whitelist (drive prefix here)
REM ============================================================

setlocal
title madmom batch (Z drive)

set WORKERS=1
set ONLY=z:
set EXCLUDE=Classics,Sleep

cd /d V:\backend || (echo ERROR: cannot cd to V:\backend ^(NUC mount missing?^) & pause & exit /b 1)

if not exist logs mkdir logs

REM Timestamp via Python (avoids cmd %date%/%time% locale traps)
for /f %%a in ('python -c "import time;print(time.strftime('%%Y%%m%%d_%%H%%M%%S'))"') do set TS=%%a
set LOG=logs\madmom_z_%TS%.log

echo === madmom batch starting ===
echo Log:       %LOG%
echo Workers:   %WORKERS%   (priority: BELOWNORMAL)
echo Filter:    --only "%ONLY%"  --exclude "%EXCLUDE%"
echo Idempotent: Ctrl+C / reboot anytime; re-run this .bat to resume.
echo.

REM start /BELOWNORMAL launches PowerShell at low priority; child python.exe
REM inherits the priority class. Tee-Object mirrors stdout to console + log
REM so you can watch live AND grep the log later.
start /BELOWNORMAL /B /WAIT "" powershell -NoProfile -Command ^
  "python -u migrate_add_dynamic_beats.py --workers %WORKERS% --only '%ONLY%' --exclude '%EXCLUDE%' 2>&1 | Tee-Object -FilePath '%LOG%'"

echo.
echo === finished (exit code %errorlevel%) ===
echo Log saved: %LOG%
echo.
echo (To resume after reboot or Ctrl+C, just double-click this .bat again.)
pause
endlocal
