@echo off
REM ============================================================
REM  High-Res MIDI Rendering Script
REM ------------------------------------------------------------
REM  Renders MIDI files to WAV using FluidSynth or Keyscape
REM  Usage: run_rendering.bat [fluidsynth|keyscape]
REM ============================================================

setlocal
title High-Res MIDI Rendering

set ENGINE=%1
if "%ENGINE%"=="" set ENGINE=fluidsynth

REM Use F:\MIDI-Experiments-Output as the output directory
set INPUT_DIR=F:\MIDI-Library
set OUTPUT_DIR=F:\MIDI-Experiments-Output

cd /d "%~dp0" || (echo ERROR: cannot cd to %~dp0 & pause & exit /b 1)

echo.
echo === High-Res Rendering starting ===
echo Input dir:  %INPUT_DIR%
echo Output dir: %OUTPUT_DIR%
echo Engine:     %ENGINE%
echo.

python scripts\render_midi_to_wav.py --input_dir "%INPUT_DIR%" --output_dir "%OUTPUT_DIR%" --engine "%ENGINE%"

echo.
echo === finished (exit code %errorlevel%) ===
pause
endlocal
