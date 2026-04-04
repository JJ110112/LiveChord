@echo off
TITLE LiveChord Accompaniment Offline Factory

echo ==================================================
echo LiveChord Offline Accompaniment Generator Factory
echo ==================================================
echo.
echo Starting batch processing...
echo This will generate L1 and L2 levels for Arpeggio and Block styles.
echo Press Ctrl+C at any time to cancel.
echo.

python backend\batch_accompaniment_worker.py --workers 12 --levels L1,L2 --styles Arpeggio,Block

echo.
echo ==================================================
echo Batch processing completed.
echo ==================================================
pause
