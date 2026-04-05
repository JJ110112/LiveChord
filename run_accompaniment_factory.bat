@echo off
title LiveChord Accompaniment Factory (Phase 11)

echo ==================================================
echo   LiveChord Accompaniment Factory (Phase 11)
echo   Includes: Section + Pedal + Dynamics
echo   Levels: L1, L2
echo   Styles: Arpeggio, Block
echo   Threads: 12
echo ==================================================
echo.
echo Press Ctrl+C to cancel.
echo.
timeout /t 3

cd /d W:\
python backend\batch_accompaniment_worker.py --workers 12 --levels L1,L2 --styles Arpeggio,Block

echo.
echo ==================================================
echo   Batch processing completed.
echo ==================================================
pause
