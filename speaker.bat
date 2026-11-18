@echo off
REM =====================================================================
REM  LiveChord PC speaker (sync follower) - no browser tab needed
REM
REM  Opens LiveChord in its own minimized Edge app window, as the sync
REM  speaker (?follow=1). The tablet (Tools -> sync -> leader) then drives
REM  playback here. Uses a separate Edge profile so it never touches your
REM  normal Edge windows, tabs or settings.
REM
REM  Usage:   speaker.bat [url]
REM  Default: http://192.168.50.6:8800/   (NUC)
REM  Extra Edge flags (testing): set LIVECHORD_SPEAKER_FLAGS=--flag=value
REM  Autostart at logon: install_speaker_startup.bat
REM =====================================================================
setlocal
set "URL=%~1"
if "%URL%"=="" set "URL=http://192.168.50.6:8800/"
set "PROFILE=%LOCALAPPDATA%\LiveChordSpeaker"
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" (
  echo Edge not found.
  exit /b 1
)

REM At logon the network / NUC may not be up yet: wait up to ~3 min for the
REM server, otherwise Edge would sit on an error page and never follow.
set /a N=0
:wait
curl -s -o nul -m 3 "%URL%" && goto run
set /a N+=1
if %N% geq 60 (
  echo LiveChord server not reachable at %URL% - opening anyway.
  goto run
)
timeout /t 3 /nobreak >nul
goto wait

:run
REM Autoplay flag: no "tap to start" overlay. The three background flags keep
REM the 200 ms drift correction running while the window is minimized.
start "" /min "%EDGE%" --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check ^
  --autoplay-policy=no-user-gesture-required ^
  --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows ^
  %LIVECHORD_SPEAKER_FLAGS% ^
  --app="%URL%?follow=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\speaker_minimize.ps1"
endlocal
