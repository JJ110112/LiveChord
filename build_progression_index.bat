@echo off
REM ====================================================================
REM  Build the Progression Library song-match index ON THE NUC.
REM
REM  Reads the local chord corpus (data\chords) and writes
REM  data\progression_index.json. Run this on the NUC desktop where
REM  data\chords is local SSD (fast, ~4 min for ~54k songs) instead of
REM  building over the SMB mount from the PC.
REM
REM  The running backend (port 8800) auto-picks-up the new index file via
REM  mtime invalidation on the next /api/progression/match request, so NO
REM  restart is needed after a rebuild.
REM ====================================================================
chcp 65001 >nul
cd /d "%~dp0"
echo Building progression index from data\chords ...
echo (this reads ~54k chord JSONs; expect a few minutes)
python tools\build_progression_index.py
echo.
echo Done. The matching-songs feature will use the new index on the next request.
pause
