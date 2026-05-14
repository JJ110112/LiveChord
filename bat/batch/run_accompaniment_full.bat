@echo off
title LiveChord Full Accompaniment Factory (All Styles)

echo ==================================================
echo   LiveChord Full Accompaniment Factory
echo   ALL Levels x ALL Styles (Phase 11)
echo   Levels: L1, L2, L3
echo   Styles: Arpeggio, Block, Rhythm, Shell, Walking
echo   Includes: Section + Pedal + Dynamics
echo   Threads: 8 (heavy workload)
echo ==================================================
echo.
echo WARNING: This generates 15 files per song!
echo Press Ctrl+C to cancel, or any key to continue.
pause >nul
echo.

cd /d V:\
python backend\batch_accompaniment_worker.py --workers 8 --levels L1,L2,L3 --styles Arpeggio,Block,Rhythm,Shell,Walking

echo.
echo ==================================================
echo   Full batch processing completed.
echo ==================================================
pause
