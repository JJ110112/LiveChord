@echo off
title LiveChord Super Worker (Phase 11 fast_mode)

echo ==================================================
echo   LiveChord Batch Super Worker
echo   System: RTX 5080 + i9-13900KF
echo   Mode: fast_mode (no adaptive_range, no onset)
echo   Threads: 24
echo   Target: Y:\ and Z:\
echo ==================================================
echo.
timeout /t 3

cd /d W:\

echo ==================================================
echo [Step 1/2] Scanning Y:\
echo ==================================================
python backend\batch_super_worker.py --root Y:\ --workers 24

echo.
echo ==================================================
echo [Step 2/2] Scanning Z:\
echo ==================================================
python backend\batch_super_worker.py --root Z:\ --workers 24

echo.
echo ==================================================
echo   All tasks completed!
echo ==================================================
pause
