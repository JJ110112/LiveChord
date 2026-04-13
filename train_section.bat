@echo off
chcp 65001 >nul
:: ===================================================
::    LiveChord Phase 13.2: DL Section Tagger Trainer
:: ===================================================

echo ===================================================
echo    LiveChord Phase 13.2: Section Detect Trainer
echo ===================================================
echo.
echo [1] Start Training BiLSTM Tagger
echo [2] Exit
echo.

set /p choice="Select mode (1 or 2): "

if "%choice%"=="1" goto train
if "%choice%"=="2" goto end

:train
echo.
echo ===================================================
echo  [STEP 1] Running BiLSTM Trainer ...
echo ===================================================
python backend\ai\train_section.py
if %errorlevel% neq 0 (
    echo [ERROR] Training failed!
    pause
    exit /b %errorlevel%
)
goto end

:end
echo.
echo ===================================================
echo All tasks finished!
echo ===================================================
pause
