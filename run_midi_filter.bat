@echo off
REM ============================================================
REM  Phase 0.5 - MIDI Quality Filter
REM ------------------------------------------------------------
REM  Scores MIDI files based on arrangement quality (0-100).
REM  Moves files scoring < 60 to F:\MIDI-Trash
REM ============================================================

setlocal
title MIDI Quality Filter

set TARGET_DIR=F:\MIDI-Library
set TRASH_DIR=F:\MIDI-Trash
set THRESHOLD=60
set WORKERS=12

cd /d "%~dp0" || (echo ERROR: cannot cd to %~dp0 & pause & exit /b 1)

echo.
echo === MIDI Quality Filter starting ===
echo Source dir: %TARGET_DIR%
echo Trash dir:  %TRASH_DIR%
echo Threshold:  %THRESHOLD%
echo Workers:    %WORKERS%
echo.
echo Warning: Low quality MIDIs will be MOVED to the trash directory.
echo Press Ctrl+C within 3 seconds to cancel...
ping 127.0.0.1 -n 4 > nul

python scripts\midi_quality_scorer.py --dir "%TARGET_DIR%" --trash "%TRASH_DIR%" --threshold %THRESHOLD% --workers %WORKERS%

echo.
echo === finished (exit code %errorlevel%) ===
pause
endlocal
