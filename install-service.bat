@echo off
REM ============================================
REM  LiveChord — 安裝為 Windows 開機自動啟動
REM  執行一次即可，之後每次開機自動啟動服務
REM ============================================

set LIVECHORD_DIR=%~dp0
set VBS_FILE=%LIVECHORD_DIR%start-hidden.vbs
set STARTUP_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LiveChord.lnk

REM 建立 VBS 隱藏啟動腳本（無黑色 CMD 視窗）
echo Creating hidden launcher...
(
echo Set WshShell = CreateObject("WScript.Shell"^)
echo WshShell.CurrentDirectory = "%LIVECHORD_DIR%backend"
echo WshShell.Run "python -m uvicorn main:app --host 0.0.0.0 --port 8800", 0, False
) > "%VBS_FILE%"

REM 建立 Startup 捷徑
echo Creating startup shortcut...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTUP_LINK%'); $s.TargetPath = '%VBS_FILE%'; $s.WorkingDirectory = '%LIVECHORD_DIR%backend'; $s.Description = 'LiveChord Server'; $s.Save()"

echo.
echo ============================================
echo  安裝完成！
echo.
echo  LiveChord 將在 Windows 開機時自動啟動
echo  服務位址: http://localhost:8800
echo.
echo  捷徑位置: %STARTUP_LINK%
echo  啟動腳本: %VBS_FILE%
echo.
echo  如需移除，執行 uninstall-service.bat
echo ============================================
pause
