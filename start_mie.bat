@echo off
REM Musical Interaction Engine launcher (ProArt 16 only, never NUC, not part of V:\).
REM   start_mie.bat                 -> scene 01, mode from the scene (SAFE)
REM   start_mie.bat --mode INTERACTIVE
REM   start_mie.bat --scene 02 --no-ui
REM   start_mie.bat --list-ports
REM UI: http://127.0.0.1:8810/mie   Console: p=PANIC r=resume b=BYPASS s=stats q=quit
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY set "PY=python"
%PY% -m backend.mie %*
pause
