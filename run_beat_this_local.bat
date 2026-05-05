@echo off
REM ============================================================
REM  Experimental — beat_this (CPJKU 2024, GPU) on G:\stage
REM ------------------------------------------------------------
REM  PyTorch transformer beat tracker on RTX 5080. ~5-10x faster
REM  than madmom per-song. Output schema matches madmom (beats[],
REM  downbeats[], bpm, tempo_curve[], beats_source="beat_this")
REM  so the player and admin UI render it without changes.
REM
REM  Idempotent — skips files already on beats_source="beat_this".
REM  Backup — first overwrite saves <hash>.json.bak.pre_beat_this.
REM  Atomic — per-file .tmp + os.replace.
REM
REM  EXPERIMENT MODE: defaults to LIMIT=30 so you can spot-check
REM  beat alignment in the player before committing to a full run.
REM  Once happy, edit LIMIT below to 0 (no cap) and re-run.
REM
REM  Output: G:\stage\livechord\logs\beat_this_<TS>.log
REM ============================================================

setlocal
title beat_this experiment (G: stage, GPU)

set STAGE_CHORDS=G:\stage\livechord\data\chords
set STAGE_LOG_DIR=G:\stage\livechord\logs
set EXCLUDE=Classics,Sleep
set LIMIT=0
set DEVICE=cuda

if not exist "%STAGE_CHORDS%" (
    echo ERROR: %STAGE_CHORDS% missing — run stage_madmom_pull.bat first.
    pause & exit /b 1
)
if not exist "%STAGE_LOG_DIR%" mkdir "%STAGE_LOG_DIR%"

REM Run from this batch's parent directory's backend/ (PC dev repo).
REM beat_this is GPU-only; V:\ is the NUC mount which has no CUDA — keep
REM this experimental script local to PC.
cd /d "%~dp0backend" || (echo ERROR: cannot cd to %~dp0backend & pause & exit /b 1)

REM Sanity check: beat_this importable + CUDA available
python -c "import torch; from beat_this.inference import File2Beats; print('beat_this OK, CUDA:', torch.cuda.is_available())"
if %errorlevel% neq 0 (
    echo.
    echo beat_this not installed or CUDA missing. Install:
    echo   pip install git+https://github.com/CPJKU/beat_this.git
    echo.
    pause & exit /b 1
)

for /f %%a in ('python -c "import time;print(time.strftime('%%Y%%m%%d_%%H%%M%%S'))"') do set TS=%%a
set LOG=%STAGE_LOG_DIR%\beat_this_%TS%.log

echo.
echo === beat_this experiment starting ===
echo Chord dir: %STAGE_CHORDS%
echo Log:       %LOG%
echo Device:    %DEVICE%   Limit: %LIMIT% (0 = no cap)
echo Filter:    --exclude "%EXCLUDE%"
echo.
echo Idempotent — Ctrl+C / shutdown anytime; re-run to resume.
echo Restoring an experiment: each processed file has a sibling
echo   .bak.pre_beat_this — manual rename restores madmom-era version.
echo.

powershell -NoProfile -Command ^
  "python -u migrate_add_beats_via_beat_this.py --device '%DEVICE%' --limit %LIMIT% --exclude '%EXCLUDE%' --chords-dir '%STAGE_CHORDS%' 2>&1 | Tee-Object -FilePath '%LOG%'"

echo.
echo === finished (exit code %errorlevel%) ===
echo Log saved: %LOG%
echo.
echo Next: open the player on a few processed songs (use ?hash=^<hash^>)
echo and watch beat dots vs audio. If aligned, raise LIMIT and rerun.
pause
endlocal
