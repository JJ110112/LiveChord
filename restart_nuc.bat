@echo off
setlocal

REM Trigger LiveChord restart on NUC via headless PowerShell script over SSH.
REM Flow:
REM   1) Copy headless restart script to NUC
REM   2) Execute script remotely in non-interactive SSH session

set "REMOTE_SCRIPT=C:\LiveChord\restart_headless.ps1"

echo Triggering LiveChord restart on NUC (192.168.50.6)...
echo.
echo [1/3] Uploading headless restart script...
scp -q "%~dp0restart_headless.ps1" "nuc:/C:/LiveChord/restart_headless.ps1"
if errorlevel 1 (
    echo Upload failed. Check ssh/scp connectivity and path permissions.
    goto :done
)

echo [2/3] Executing headless restart...
ssh nuc "cmd /c powershell -NoProfile -ExecutionPolicy Bypass -File %REMOTE_SCRIPT%"
echo [3/3] Probing service health...
powershell -NoProfile -Command "$ok=$false; for($i=0; $i -lt 10; $i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://192.168.50.6:8800/' -Method Get -TimeoutSec 5; if($r.StatusCode -eq 200){ $ok=$true; break } } catch {} ; Start-Sleep -Milliseconds 800 }; if($ok){ exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo Headless restart did not recover service in time.
    echo Attempting fallback task trigger \LiveChordRestart ...
    ssh nuc "schtasks /run /tn \LiveChordRestart"
    if errorlevel 1 (
        echo Fallback task trigger also failed.
        goto :done
    )
) else (
    echo Headless restart recovered service.
)

echo Restart command sent.

echo.
echo Restart triggered successfully.

:done
echo.
echo Verify with: curl http://192.168.50.6:8800/
endlocal
