@echo off
REM ===========================================================================
REM Step 1 / 3 : Extract audio features for the beat_refiner training corpus.
REM
REM Reads:  V:\data\chords\ (manifest), Y:\ + Z:\ (audio source, NAS)
REM Writes: G:\livechord\features\ (~60 GB of .npz, local SSD speed)
REM
REM Idempotent: already-cached songs are skipped, so Ctrl-C and rerun is safe.
REM Tune --workers if 8 saturates your disk or NAS bandwidth.
REM
REM Expected wall time: ~5-8 hours on RTX 5080 box reading from SMB NAS.
REM ===========================================================================

setlocal
cd /d "%~dp0"

echo.
echo === Step 1/3: Feature Extraction ===
echo Started: %date% %time%
echo.

if not exist "G:\livechord"          mkdir "G:\livechord"
if not exist "G:\livechord\features" mkdir "G:\livechord\features"
if not exist "G:\livechord\logs"     mkdir "G:\livechord\logs"

set TIMESTAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOGFILE=G:\livechord\logs\extract_%TIMESTAMP%.log

echo Logging to: %LOGFILE%
echo.

python -u scripts\extract_features_batch.py ^
    --splits-dir backend/ai/splits ^
    --cache-root G:/livechord/features ^
    --workers 8 ^
    --out-report G:/livechord/extract_report.json

set EXITCODE=%errorlevel%

echo.
if %EXITCODE% NEQ 0 (
    echo *** FAILED with exit code %EXITCODE% ***
    echo Check the console output above and the manifest at backend\ai\splits\manifest.json
) else (
    echo === Step 1 done at %date% %time% ===
    echo Cache size + count:
    powershell -NoProfile -Command "$f=Get-ChildItem G:\livechord\features -Recurse -File -ErrorAction SilentlyContinue; $mb=([math]::Round((($f ^| Measure-Object Length -Sum).Sum)/1MB,1)); Write-Host \"  $($f.Count) npz files, $mb MB total\""
    echo.
    echo Next: run train_refiner_2_train.bat
)

pause
endlocal
exit /b %EXITCODE%
