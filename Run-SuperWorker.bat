@echo off
title LiveChord Super Worker (Phase 11 fast_mode)

echo ==================================================
echo   LiveChord Batch Super Worker
echo   System: RTX 5080 16GB + i9-13900KF 24C/32T
echo   Mode: fast_mode
echo   CPU Threads: 24  GPU Concurrent: 6
echo ==================================================
echo.
timeout /t 3

cd /d V:\backend

echo ==================================================
echo [Step 1/2] Scanning Y drive
echo ==================================================
python batch_super_worker.py --root Y:/ --workers 24 --gpu-concurrent 6
echo Step 1 exit code: %errorlevel%

echo.
echo ==================================================
echo [Step 2/2] Scanning Z drive
echo ==================================================
python batch_super_worker.py --root Z:/ --workers 24 --gpu-concurrent 6
echo Step 2 exit code: %errorlevel%

echo.
echo ==================================================
echo   All tasks completed!
echo ==================================================
pause
