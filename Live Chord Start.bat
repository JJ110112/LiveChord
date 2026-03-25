@echo off
echo ==========================================
echo   LiveChord - Starting...
echo ==========================================
cd /d "C:\Users\hitea\LiveChord\backend"
python -m uvicorn main:app --host 0.0.0.0 --port 8800 --reload
pause
