@echo off

set "HISTORY_FILE=%~dp0data\.local_music_root"
set "LAST_ROOT="

if exist "%HISTORY_FILE%" (
    set /p LAST_ROOT=<"%HISTORY_FILE%"
)

echo ==========================================
echo   LiveChord - Local Folder Playback
echo ==========================================
echo.
if not "%LAST_ROOT%"=="" (
    echo Last used folder: %LAST_ROOT%
    echo Press ENTER to use this folder, or enter a new absolute path.
) else (
    echo Please enter the absolute path to your local music folder.
)
echo (For example: C:\Users\hitea\Downloads\test_music)
echo Or directly drag and drop the folder into this window.
echo.

set "LOCAL_MUSIC_ROOT="
set /p LOCAL_MUSIC_ROOT="Folder path: "

if "%LOCAL_MUSIC_ROOT%"=="" (
    if "%LAST_ROOT%"=="" (
        echo Path cannot be empty! Exiting...
        pause
        exit /b
    ) else (
        set "LOCAL_MUSIC_ROOT=%LAST_ROOT%"
    )
)

REM Remove double quotes that might be added by drag-and-drop
set LOCAL_MUSIC_ROOT=%LOCAL_MUSIC_ROOT:"=%

REM Remove trailing spaces (batch file trick to clear trailing whitespace left by set /p)
for /f "tokens=* delims= " %%a in ("%LOCAL_MUSIC_ROOT%") do set LOCAL_MUSIC_ROOT=%%a

echo %LOCAL_MUSIC_ROOT%>"%HISTORY_FILE%"

echo.
echo ==========================================
echo Target Folder: %LOCAL_MUSIC_ROOT%
echo ==========================================

set LIVECHORD_MUSIC_ROOT=%LOCAL_MUSIC_ROOT%

cd /d "%~dp0backend"
echo Starting LiveChord...
python -m uvicorn main:app --host 0.0.0.0 --port 8800 --reload
pause
