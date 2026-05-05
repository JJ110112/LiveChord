@echo off
REM ============================================================
REM  Post-migration trigger — tier2 backup on NUC 8800
REM ------------------------------------------------------------
REM  Captures the new sharded V:\data\chords + melodies + models
REM  into W:\LiveChord\backup\tier2\<ts>\ as the first restore-point
REM  for the bucketed layout.
REM
REM  Pre-condition: restart_dual.bat has been run on the NUC and
REM  both 8800 + 8801 are responding (give it ~15s after restart).
REM
REM  LAN-bypass: 192.168.x.x is auto-authenticated as admin by
REM  auth_api.get_admin_user, so no token / login needed.
REM
REM  Idempotent: re-runnable; each call writes a fresh timestamped
REM  snapshot. Safe to Ctrl+C — the NUC keeps running the backup
REM  in the background; status is just a poll, not the worker.
REM ============================================================

setlocal
title post-migration tier2 backup

cd /d "%~dp0"
python scripts\trigger_nuc_backup.py --tier tier2

set RC=%errorlevel%
echo.
if %RC%==0 (
    echo === Backup complete. Bucketed snapshot saved as new restore-point. ===
) else (
    echo === Backup ended with code %RC% — check NUC admin UI for errors. ===
)
pause
endlocal
exit /b %RC%
