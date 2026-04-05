@echo off
title LiveChord Hybrid Extraction Worker (Phase 12)

echo ==================================================
echo   LiveChord Hybrid Extraction Worker (Phase 12)
echo   Architecture: Audio-Informed Generation
echo   Engine: Demucs (Stem Separation) + Basic Pitch
echo ==================================================
echo.
echo WARNING: This is a computationally intensive process.
echo It will scan the library and extract Bass/Vocals MIDI
echo for songs that have chords.json but no hybrid MIDI yet.
echo.
echo Press Ctrl+C to cancel.
echo.
timeout /t 5

cd /d "%~dp0"
python backend\batch_hybrid_worker.py

echo.
echo ==================================================
echo   Hybrid extraction completed.
echo ==================================================
pause
