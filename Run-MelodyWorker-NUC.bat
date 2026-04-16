@echo off
title LiveChord Melody Worker (NUC - CPU Only)

echo ==================================================
echo   LiveChord Melody Extraction Worker
echo   Device: NUC (CPU only, no GPU)
echo   Mode: melody-only (pYIN)
echo   CPU Threads: 4
echo ==================================================
echo.
echo This extracts melodies using pYIN (CPU-only).
echo Chord detection runs separately on PC (GPU).
echo.
timeout /t 3

cd /d V:\backend

echo ==================================================
echo [Step 1/2] Scanning Y drive
echo ==================================================
python batch_super_worker.py --root Y:/ --mode melody --workers 4 --progress-file V:/data/.extraction_progress_nuc.json --stop-file V:/data/.extraction_stop_nuc
echo Step 1 exit code: %errorlevel%

echo.
echo ==================================================
echo [Step 2/2] Scanning Z drive
echo ==================================================
python batch_super_worker.py --root Z:/ --mode melody --workers 4 --progress-file V:/data/.extraction_progress_nuc.json --stop-file V:/data/.extraction_stop_nuc
echo Step 2 exit code: %errorlevel%

echo.
echo ==================================================
echo   Melody extraction completed!
echo ==================================================
pause
