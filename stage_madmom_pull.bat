@echo off
REM ============================================================
REM  Stage step 1/3 — pull V:\data\chords -> G:\stage\... (local SSD)
REM ------------------------------------------------------------
REM  Run this once before run_madmom_local.bat. Re-runnable: robocopy
REM  /MIR brings staging back in sync with V:\, including newly-added
REM  files since the last pull. WARNING: /MIR also DELETES files in
REM  staging that no longer exist in V:\ — but since V:\ is the
REM  source of truth that's the desired behaviour.
REM ============================================================

setlocal
title stage pull (V: -> G:)

set SRC=V:\data\chords
set DST=G:\stage\livechord\data\chords

echo === stage pull starting ===
echo Source:      %SRC%
echo Destination: %DST%
echo (This is the slow SMB read — ~78k JSONs, expect a few minutes.)
echo.

if not exist G:\ (
    echo ERROR: G:\ drive not present.
    pause & exit /b 1
)
if not exist "%SRC%" (
    echo ERROR: %SRC% missing — is V:\ mounted?
    pause & exit /b 1
)

if not exist "%DST%" mkdir "%DST%"

robocopy "%SRC%" "%DST%" *.json /MIR /MT:8 /R:2 /W:5 /NFL /NDL /NP

REM robocopy exit codes: 0=no change, 1=files copied OK, 2=extra in dst, 3=1+2.
REM Anything >=8 is a real failure.
set RC=%errorlevel%
if %RC% GEQ 8 (
    echo.
    echo ERROR: robocopy failed with code %RC%
    pause & exit /b %RC%
)

echo.
echo === pull done (robocopy code %RC%) ===
echo Next step: run_madmom_local.bat
pause
endlocal
