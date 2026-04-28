@echo off
title AI Arranger - Generation Pipeline

set ROLE=BASS
set CHORD=[CH_C:maj]
set OUT_MID=eval_output\ai_bass.mid
set OUT_WAV=eval_output\ai_bass.wav

echo =======================================================
echo  LiveChord AI - E2E Generation Pipeline
echo =======================================================
echo.
echo Target Role   : %ROLE%
echo Constraint    : %CHORD%
echo Output MIDI   : %OUT_MID%
echo Output WAV    : %OUT_WAV%
echo.

python scripts\generate_and_render.py --role %ROLE% --chord "%CHORD%" --out_mid "%OUT_MID%" --out_wav "%OUT_WAV%"

echo.
pause
