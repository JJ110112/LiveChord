@echo off
title LiveChord Chord Worker (PC - GPU Only)

echo ==================================================
echo   LiveChord Chord Detection Worker
echo   Device: PC (RTX 5080 + i9-13900KF)
echo   Mode: chord-only (BTC GPU)
echo   CPU Threads: 24  GPU Concurrent: 6
echo ==================================================
echo.
echo This runs BTC chord detection (GPU-only).
echo Melody extraction runs separately on NUC (CPU).
echo.
timeout /t 3

cd /d V:\backend

echo ==================================================
echo [Step 1/2] Scanning Y drive
echo ==================================================
python batch_super_worker.py --root Y:/ --mode chord --workers 24 --gpu-concurrent 6
echo Step 1 exit code: %errorlevel%

echo.
echo ==================================================
echo [Step 2/2] Scanning Z drive
echo ==================================================
python batch_super_worker.py --root Z:/ --mode chord --workers 24 --gpu-concurrent 6
echo Step 2 exit code: %errorlevel%

echo.
echo ==================================================
echo   Chord detection completed!
echo ==================================================
pause
