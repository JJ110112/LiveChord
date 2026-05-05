@echo off
REM ============================================================
REM  Experimental — beat_this parallel processing
REM ------------------------------------------------------------
REM  Run multiple GPU workers to process F:\MIDI-Library
REM  Usage: run_beat_parallel.bat [LIMIT]
REM ============================================================

setlocal
title Parallel beat_this (F:\MIDI-Library)

set TARGET_DIR=F:\MIDI-Library
set LIMIT=%1
if "%LIMIT%"=="" set LIMIT=0

set WORKERS=4
set DEVICE=cuda

cd /d "%~dp0" || (echo ERROR: cannot cd to %~dp0 & pause & exit /b 1)

echo.
echo === Parallel beat_this experiment starting ===
echo Target dir: %TARGET_DIR%
echo Workers:    %WORKERS%
echo Device:     %DEVICE%
echo Limit:      %LIMIT% (0 = no cap)
echo.
echo Idempotent — Ctrl+C / shutdown anytime; re-run to resume.
echo.

python scripts\run_beat_this_parallel.py --dir "%TARGET_DIR%" --workers %WORKERS% --device %DEVICE% --limit %LIMIT%

echo.
echo === finished (exit code %errorlevel%) ===
pause
endlocal
