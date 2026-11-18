@echo off
REM =====================================================================
REM  Install / remove the LiveChord PC speaker from Windows startup.
REM
REM  install_speaker_startup.bat           add (tray speaker at logon)
REM  install_speaker_startup.bat remove    take it out again
REM
REM  Uses the per-user Startup folder (no admin rights needed). The entry
REM  is a .vbs so no console window flashes at logon.
REM =====================================================================
setlocal
set "DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%DIR%\LiveChordSpeaker.vbs"
REM older versions installed a .cmd
del "%DIR%\LiveChordSpeaker.cmd" 2>nul

if /i "%~1"=="remove" (
  del "%LINK%" 2>nul
  echo Removed: %LINK%
  goto done
)

> "%LINK%" echo CreateObject("WScript.Shell").Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""%~dp0tools\speaker_tray.ps1""", 0, False
echo Installed: %LINK%
echo It will start the tray speaker at next logon. Run speaker.bat now to start it immediately.

:done
pause
endlocal
