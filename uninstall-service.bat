@echo off
REM ============================================
REM  LiveChord — 移除 Windows 開機自動啟動
REM ============================================

set LIVECHORD_DIR=%~dp0
set VBS_FILE=%LIVECHORD_DIR%start-hidden.vbs
set STARTUP_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LiveChord.lnk

if exist "%STARTUP_LINK%" (
    del "%STARTUP_LINK%"
    echo Removed startup shortcut
) else (
    echo Startup shortcut not found
)

if exist "%VBS_FILE%" (
    del "%VBS_FILE%"
    echo Removed hidden launcher
) else (
    echo Hidden launcher not found
)

echo.
echo ============================================
echo  已移除 LiveChord 開機自動啟動
echo ============================================
pause
