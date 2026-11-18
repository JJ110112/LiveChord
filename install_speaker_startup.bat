@echo off
REM =====================================================================
REM  Install / remove the LiveChord PC speaker from Windows startup.
REM
REM  install_speaker_startup.bat           add (runs speaker.bat at logon)
REM  install_speaker_startup.bat remove    take it out again
REM
REM  Uses the per-user Startup folder (no admin rights needed).
REM =====================================================================
setlocal
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LiveChordSpeaker.cmd"

if /i "%~1"=="remove" (
  del "%LINK%" 2>nul
  echo Removed: %LINK%
  goto done
)

> "%LINK%" echo @call "%~dp0speaker.bat"
echo Installed: %LINK%
echo It will start the speaker at next logon. Run speaker.bat now to start it immediately.

:done
pause
endlocal
