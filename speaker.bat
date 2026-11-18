@echo off
REM =====================================================================
REM  LiveChord PC speaker (sync follower) - lives in the system tray
REM
REM  Starts tools\speaker_tray.ps1: a tray icon that runs LiveChord as the
REM  sync speaker (?follow=1) in a hidden Edge app window. The tablet
REM  (Tools -> sync -> leader) then drives playback here. Uses a separate
REM  Edge profile so it never touches your normal Edge windows or tabs.
REM  Tray icon: double-click = show / hide the player, right-click = menu
REM  (restart speaker, quit).
REM
REM  Usage:   speaker.bat [url]
REM  Default: http://192.168.50.6:8800/   (NUC)
REM  Extra Edge flags (testing): set LIVECHORD_SPEAKER_FLAGS=--flag=value
REM  Autostart at logon: install_speaker_startup.bat
REM =====================================================================
setlocal
set "URL=%~1"
if "%URL%"=="" set "URL=http://192.168.50.6:8800/"
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\speaker_tray.ps1" -Url "%URL%"
endlocal
