@echo off
REM ============================================================
REM  Post-batch sharding migration (run AFTER beat_this batch finishes)
REM ------------------------------------------------------------
REM  Migrates two flat chord folders into 2-char prefix buckets:
REM    1. G:\stage\livechord\data\chords  (PC staging, ~200k files)
REM    2. C:\...\LiveChord\data\chords    (dev/8803 reads from here)
REM
REM  Idempotent: re-running is safe; already-sharded files are skipped.
REM  Uses os.replace (atomic rename), no copy — fast even on 200k files.
REM
REM  Does NOT push to V:\ — that's a separate explicit step
REM  (stage_madmom_push.bat) so you can spot-check 8803 first.
REM ============================================================

setlocal
title post-batch sharding migration
cd /d "%~dp0backend"

set DEV_CHORDS=%~dp0data\chords
set STAGE_CHORDS=G:\stage\livechord\data\chords

echo ==================================================
echo  Sharding migration: flat -> 2-char prefix buckets
echo ==================================================
echo.

if not exist "%STAGE_CHORDS%" (
    echo ERROR: %STAGE_CHORDS% missing
    pause & exit /b 1
)
if not exist "%DEV_CHORDS%" (
    echo ERROR: %DEV_CHORDS% missing
    pause & exit /b 1
)

echo === Step 1/2: G:\stage  ===
echo Path: %STAGE_CHORDS%
echo (Expect ~3-5 min on local SSD for ~200k files)
echo.
python migrate_shard_chords.py --chords-dir "%STAGE_CHORDS%"
if errorlevel 1 (
    echo.
    echo ERROR: G:\stage migration failed
    pause & exit /b 1
)
echo.

echo === Step 2/2: dev/data/chords ===
echo Path: %DEV_CHORDS%
echo (Expect a few seconds for ~53k files)
echo.
python migrate_shard_chords.py --chords-dir "%DEV_CHORDS%"
if errorlevel 1 (
    echo.
    echo ERROR: dev migration failed
    pause & exit /b 1
)
echo.

echo ==================================================
echo  Migration done. Next steps:
echo ==================================================
echo.
echo  1. Restart 8803 (Ctrl+C the existing 8803 window
echo     then run: start_personal_local.bat)
echo.
echo  2. Spot-check on 8803:
echo     http://localhost:8803/player?path=...
echo     Verify chord data loads, switch buttons work
echo.
echo  3. If 8803 OK, push to V:\:
echo     stage_madmom_push.bat  (now syncs sharded layout, then prompts
echo                              to trigger tier2 backup once NUC is back)
echo.
echo  4. Restart NUC service:
echo     restart.bat   (on NUC console — single 8800, no more beta)
echo.
echo  5. (auto, via stage_madmom_push.bat tail) tier2 backup snapshot
echo     of new bucketed layout — first restore-point for sharded data.
echo     Re-runnable standalone via: backup_after_migration.bat
echo.
pause
endlocal
