@echo off
title LiveChord Super Worker (Phase 11 fast_mode)

echo ==================================================
echo   LiveChord Batch Super Worker
echo   System: RTX 5080 (16GB) + i9-13900KF (24C/32T)
echo   Mode: fast_mode (no adaptive_range, no onset)
echo   CPU Threads: 24  |  GPU Concurrent: 6
echo ==================================================
echo.
timeout /t 3

cd /d W:\

echo ==================================================
echo [Step 1/2] Scanning Y:\
echo ==================================================
python backend\batch_super_worker.py --root "Y:\" --workers 24 --gpu-concurrent 6
if %errorlevel% neq 0 (
    echo [ERROR] Step 1 failed with code %errorlevel%
)

echo.
echo ==================================================
echo [Step 2/2] Scanning Z:\
echo ==================================================
python backend\batch_super_worker.py --root "Z:\" --workers 24 --gpu-concurrent 6
if %errorlevel% neq 0 (
    echo [ERROR] Step 2 failed with code %errorlevel%
)

echo.
echo ==================================================
echo   All tasks completed!
echo ==================================================
pause
