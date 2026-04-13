@echo off
chcp 65001 >nul
title LiveChord AI Trainer

echo ===================================================
echo     LiveChord Phase 13: Transformer Trainer
echo ===================================================
echo.
echo [1] Full Training: Tokenizer + Chord2Vec + Transformer
echo [2] Fast Fine-Tune: Skip preprocessing, run Transformer only
echo.
set /p choice="Select mode (1 or 2): "

set PYTHONUTF8=1

if "%choice%"=="1" (
    echo.
    echo ===================================================
    echo   [STEP 1] Running Tokenizer (Multi-threaded)
    echo ===================================================
    python backend\ai\tokenizer.py "W:\data\chords"
    
    echo.
    echo ===================================================
    echo   [STEP 2] Running Chord2Vec (SVD matrix prep)
    echo ===================================================
    python backend\ai\chord2vec.py
)

echo.
echo ===================================================
echo   [STEP 3] Running Seq2Seq Transformer Training
echo ===================================================
python backend\ai\transformer_trainer.py

echo.
echo ===================================================
echo All training pipelines completed!
echo The generated model is saved at W:\data\models\
echo ===================================================
pause
