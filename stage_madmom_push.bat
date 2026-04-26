@echo off
REM ============================================================
REM  Stage step 3/3 — push G:\stage\... -> V:\data\chords
REM ------------------------------------------------------------
REM  PRECONDITION: NUC service 8800 stopped — production
REM  must not write new chord JSONs while we mirror. /MIR mirrors
REM  the source tree exactly: extras on dest are DELETED.
REM
REM  Sharded layout (post-migrate_shard_chords): source has
REM  G:\stage\livechord\data\chords\<bucket>/<hash>.json[.bak.X].
REM  Robocopy /MIR recurses sub-buckets and mirrors them onto V:\.
REM  V:\ ends up as exact copy: 256 bucket dirs, 0 flat files.
REM ============================================================

setlocal
title stage push (G: -> V:)

set SRC=G:\stage\livechord\data\chords
set DST=V:\data\chords

echo === stage push starting ===
echo Source:      %SRC%
echo Destination: %DST%
echo.
echo PRECONDITION: 8800 stopped (otherwise concurrent writes risk).
echo Running with /MIR — extra files in V:\ NOT also in G:\ will be DELETED.
echo If you want safer "only update / never delete", edit this batch:
echo   change /MIR to: /E /XO
echo.
choice /M "Proceed with /MIR push"
if errorlevel 2 (
    echo Aborted.
    pause & exit /b 1
)

if not exist "%SRC%" (
    echo ERROR: %SRC% missing — nothing to push.
    pause & exit /b 1
)
if not exist "%DST%" (
    echo ERROR: %DST% missing — is V:\ mounted?
    pause & exit /b 1
)

REM No file filter — copy ALL files (.json AND .json.bak.* sidecars).
REM Earlier "*.json" filter would have skipped .bak.beat_this etc, breaking
REM the 3-way switch on V:\ because the bak caches wouldn't transfer.
robocopy "%SRC%" "%DST%" /MIR /MT:8 /R:2 /W:5 /NFL /NDL /NP

set RC=%errorlevel%
if %RC% GEQ 8 (
    echo.
    echo ERROR: robocopy failed with code %RC%
    pause & exit /b %RC%
)

echo.
echo === push done (robocopy code %RC%) ===
echo.
echo Next: restart 8800 on NUC via restart.bat, wait ~15s for it to come back.
echo (No more beta — restart_dual.bat would also bring up 8801, don't use it.)
echo.
choice /M "Has restart.bat finished and 8800 responds"
if errorlevel 2 (
    echo Skipping post-migration backup. Re-run backup_after_migration.bat later when ready.
    pause & exit /b 0
)

echo.
echo === Triggering tier2 backup on NUC (bucketed-layout restore point) ===
call "%~dp0backup_after_migration.bat"
endlocal
