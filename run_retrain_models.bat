@echo off
title LiveChord AI Model Retrain

echo ==================================================
echo   LiveChord AI Model Retrain
echo   Markov + Chord2Vec + Groove + Emission + Trans
echo ==================================================
echo.
echo This will retrain all AI models from current data.
echo Press Ctrl+C to cancel.
echo.
timeout /t 3

cd /d W:\backend
python -c "import requests; r = requests.post('http://localhost:8800/api/ai/retrain'); print(r.json())"

echo.
echo ==================================================
echo   Retrain completed.
echo ==================================================
pause
