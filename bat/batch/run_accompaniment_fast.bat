@echo off
title LiveChord Accompaniment Factory (Fast - No Pedal/Dynamics)

echo ==================================================
echo   LiveChord Accompaniment Factory (Fast Mode)
echo   Levels: L1, L2
echo   Styles: Arpeggio, Block
echo   Pedal: OFF  |  Dynamics: OFF
echo   Threads: 16 (lightweight)
echo ==================================================
echo.
timeout /t 3

cd /d V:\
python backend\batch_accompaniment_worker.py --workers 16 --levels L1,L2 --styles Arpeggio,Block --no-pedal --no-dynamics

echo.
echo ==================================================
echo   Fast batch completed.
echo ==================================================
pause
