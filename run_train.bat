@echo off
title Neural Arranger Training

set DATA_DIR=F:\MIDI-Library
set EPOCHS=50
set BATCH_SIZE=8
set DEVICE=cuda

echo =======================================================
echo  LiveChord AI - Phase 2: Neural Arranger Training
echo =======================================================
echo.
echo Dataset Directory : %DATA_DIR%
echo Target Epochs     : %EPOCHS%
echo Batch Size        : %BATCH_SIZE%
echo Compute Device    : %DEVICE%
echo.
echo Press Ctrl+C at any time to stop training safely.
echo.

python scripts\run_training.py --data_dir "%DATA_DIR%" --epochs %EPOCHS% --batch_size %BATCH_SIZE% --device %DEVICE%

echo.
echo === Training session ended ===
pause
