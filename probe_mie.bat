@echo off
REM MIE Phase 0 probe launcher (performance PC only, never NUC).
REM   probe_mie.bat            -> echo test (config: data\mie\ports.json)
REM   probe_mie.bat list       -> print all MIDI ports
REM   probe_mie.bat bypass     -> T0 listen-only test
REM   probe_mie.bat panic      -> send PANIC and exit
cd /d "%~dp0"

REM Locate a real CPython. The Microsoft Store "python.exe" alias shadows PATH
REM in shells opened before Python was installed, so prefer the py launcher,
REM then the per-user python.org install, then whatever "python" resolves to.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY set "PY=python"

if "%~1"=="" (
    %PY% backend\mie\probe.py run
) else if /i "%~1"=="bypass" (
    %PY% backend\mie\probe.py run --bypass
) else (
    %PY% backend\mie\probe.py %*
)
pause
