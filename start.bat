@echo off
echo ==========================================
echo   LiveChord - Starting...
echo ==========================================
cd /d "%~dp0backend"
python -m uvicorn main:app --host 0.0.0.0 --port 8800 --reload
pause
