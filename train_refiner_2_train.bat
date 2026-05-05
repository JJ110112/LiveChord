@echo off
REM ===========================================================================
REM Step 2 / 3 : Train the beat_refiner Compact Transformer.
REM
REM Reads:  G:\livechord\features\ (cache from Step 1, ~60 GB)
REM         backend\ai\splits\ (manifest, train/val/test JSONs)
REM Writes: G:\livechord\models\beat_refiner.pt (best checkpoint)
REM         G:\livechord\models\history.json (per-epoch metrics)
REM
REM Expected wall time: ~6-10 hours on RTX 5080.
REM Best checkpoint is keyed by val gold|gold downbeat_f1.
REM
REM Tune --batch-size if you OOM (try 16) or have headroom (try 48).
REM Tune --epochs: 30 is a reasonable default for a 13k-song trainable set.
REM ===========================================================================

setlocal
cd /d "%~dp0"

echo.
echo === Step 2/3: Train BeatRefiner ===
echo Started: %date% %time%
echo.

if not exist "G:\livechord\models" mkdir "G:\livechord\models"
if not exist "G:\livechord\logs"   mkdir "G:\livechord\logs"

if not exist "G:\livechord\features" (
    echo *** ERROR: G:\livechord\features\ does not exist.
    echo *** Run train_refiner_1_extract.bat first.
    pause
    exit /b 1
)

set TIMESTAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOGFILE=G:\livechord\logs\train_%TIMESTAMP%.log

echo Logging to: %LOGFILE%
echo.

python -u scripts\train_beat_refiner.py --train ^
    --splits-dir backend/ai/splits ^
    --cache-root G:/livechord/features ^
    --out G:/livechord/models ^
    --device cuda ^
    --epochs 30 ^
    --batch-size 32 ^
    --segment-frames 646 ^
    --warmup-steps 500 ^
    --lr 3e-4 ^
    --log-every 20

set EXITCODE=%errorlevel%

echo.
if %EXITCODE% NEQ 0 (
    echo *** FAILED with exit code %EXITCODE% ***
    echo Common causes:
    echo   - CUDA OOM: rerun with --batch-size 16 (edit this .bat)
    echo   - cache miss: confirm Step 1 finished completely
) else (
    echo === Step 2 done at %date% %time% ===
    echo.
    echo Saved checkpoint:  G:\livechord\models\beat_refiner.pt
    echo Saved history:     G:\livechord\models\history.json
    echo.
    echo Next: run train_refiner_3_deploy.bat to copy to V:\data\models\
)

pause
endlocal
exit /b %EXITCODE%
