@echo off
REM MIE Phase 0 probe launcher (performance PC only, never NUC).
REM   probe_mie.bat            -> echo test (config: data\mie\ports.json)
REM   probe_mie.bat list       -> print all MIDI ports
REM   probe_mie.bat bypass     -> T0 listen-only test
REM   probe_mie.bat panic      -> send PANIC and exit
cd /d "%~dp0"
if "%~1"=="" (
    python backend\mie\probe.py run
) else if /i "%~1"=="bypass" (
    python backend\mie\probe.py run --bypass
) else (
    python backend\mie\probe.py %*
)
pause
