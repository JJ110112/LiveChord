@echo off
REM Trigger LiveChord restart on NUC via SSH.
REM
REM Direct `ssh nuc "C:\LiveChord\restart.bat"` does NOT work — SSH lands in
REM Windows session 0 (no desktop) and the `start "title" python ...` inside
REM restart.bat needs an interactive session to spawn the uvicorn console.
REM Workaround: trigger a pre-registered scheduled task (`LiveChordRestart`,
REM /ru hitea /it) which runs restart.bat in hitea's interactive desktop
REM session. See NUC setup prompt in CLAUDE.md / chat history.
REM
REM Requires:
REM   - OpenSSH Server enabled on NUC + key-based auth (one-time)
REM   - "nuc" host alias in %USERPROFILE%\.ssh\config
REM   - Scheduled task `LiveChordRestart` registered on NUC
REM   - hitea logged in to NUC desktop (interactive task requirement)

echo Triggering LiveChord restart on NUC (192.168.50.6)...
echo.

ssh nuc "schtasks /run /tn LiveChordRestart"

if errorlevel 1 (
    echo.
    echo Trigger failed. Common causes:
    echo   - sshd not running on NUC
    echo   - Scheduled task `LiveChordRestart` not registered
    echo   - hitea not logged in to NUC desktop ^(/it tasks require active session^)
    echo.
)

echo.
echo Done. Verify with: curl http://192.168.50.6:8800/
pause
