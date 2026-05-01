@echo off
REM ============================================================
REM  PC startup-delayed batch: beat_this on V:\data\chords
REM ------------------------------------------------------------
REM  - Waits 10 minutes after PC boot (let user settle in first)
REM  - Runs at BELOW NORMAL priority so it can't lag the desktop
REM  - 3 GPU workers (configurable via WORKERS below)
REM  - Idempotent: only processes JSONs lacking beats_source=beat_this
REM
REM  Install (run once on PC):
REM    1. Press Win+R, type:  shell:startup
REM    2. Drop a shortcut to THIS .bat into that folder
REM       (right-click the .bat -> Send to -> Desktop, then move
REM        the desktop shortcut into the startup folder)
REM
REM  Manual run (skip the 10-min wait):
REM    batch_beat_this_pc_startup.bat NOW
REM
REM  Stop:
REM    Close the console window OR Ctrl+C. Re-run is safe (idempotent).
REM
REM  Output:
REM    Per-run log -> data\logs\batch_beat_this_<TS>.log
REM    Live progress -> data\logs\batch_beat_this_progress.json
REM ============================================================

setlocal
title beat_this batch (V:\data\chords, GPU)

set REPO_ROOT=%~dp0
set CHORDS_DIR=V:\data\chords
set WORKERS=3
set DEVICE=cuda
set LIMIT=0
set DELAY_SECONDS=600

REM Allow "NOW" as first arg to skip the startup delay
if /i "%~1"=="NOW" set DELAY_SECONDS=0

if not exist "%CHORDS_DIR%" (
    echo ERROR: %CHORDS_DIR% not mounted. Make sure V:\ is online.
    pause & exit /b 1
)

set LOG_DIR=%REPO_ROOT%data\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM 10-minute wait at boot — gives Windows / antivirus / sync clients
REM time to finish their startup churn so this batch doesn't dogpile.
if %DELAY_SECONDS% gtr 0 (
    echo.
    echo === beat_this batch will start in %DELAY_SECONDS% seconds ===
    echo Press Ctrl+C to cancel, or run with NOW arg to skip the wait.
    echo.
    timeout /t %DELAY_SECONDS% /nobreak >nul
    if errorlevel 1 (
        echo.
        echo Cancelled by user.
        exit /b 0
    )
)

REM Sanity check: V:\ still mounted after the wait
if not exist "%CHORDS_DIR%" (
    echo ERROR: %CHORDS_DIR% disappeared during the wait. Aborting.
    exit /b 1
)

REM Sanity check: beat_this importable + CUDA available
python -c "import torch; from beat_this.inference import File2Beats; print('beat_this OK, CUDA:', torch.cuda.is_available())"
if %errorlevel% neq 0 (
    echo.
    echo beat_this not installed or CUDA missing. Install:
    echo   pip install git+https://github.com/CPJKU/beat_this.git
    echo.
    pause & exit /b 1
)

for /f %%a in ('python -c "import time;print(time.strftime('%%Y%%m%%d_%%H%%M%%S'))"') do set TS=%%a
set LOG=%LOG_DIR%\batch_beat_this_%TS%.log

echo.
echo === beat_this batch starting ===
echo Chord dir: %CHORDS_DIR%
echo Workers:   %WORKERS%   Device: %DEVICE%   Limit: %LIMIT% (0 = all)
echo Log:       %LOG%
echo Priority:  BELOW NORMAL (won't fight foreground apps)
echo.

REM Run at BELOW NORMAL priority so the GPU batch can't make the PC feel laggy
REM during desktop work. /WAIT keeps this console attached for log streaming.
start "beat_this batch" /WAIT /BELOWNORMAL /B powershell -NoProfile -Command ^
  "python -u '%REPO_ROOT%scripts\batch_beat_this_pc.py' --chords-dir '%CHORDS_DIR%' --workers %WORKERS% --device '%DEVICE%' --limit %LIMIT% 2>&1 | Tee-Object -FilePath '%LOG%'"

echo.
echo === finished (exit code %errorlevel%) ===
echo Log saved: %LOG%
echo Progress JSON: %LOG_DIR%\batch_beat_this_progress.json
echo.

REM No pause when launched from Startup folder — let the window self-close.
REM Set INTERACTIVE=1 in the env if you want a pause for manual runs.
if defined INTERACTIVE pause
endlocal
