@echo off
REM ===========================================================================
REM Step 3 / 3 : Copy training artifacts from G:\ (local) to V:\ (NUC runtime).
REM
REM Reads:  G:\livechord\models\beat_refiner.pt
REM         G:\livechord\models\history.json
REM Writes: V:\data\models\beat_refiner.pt
REM         V:\data\models\history.json
REM         data\models\beat_refiner.pt (PC-side git copy, optional commit)
REM
REM Does NOT copy the feature cache — keep it on G:\ for fast re-train. Copy
REM manually if NUC needs it (it doesn't, the refiner runs inference only).
REM
REM Run AFTER Step 2 succeeded.
REM ===========================================================================

setlocal
cd /d "%~dp0"

echo.
echo === Step 3/3: Deploy artifacts to V:\ ===
echo Started: %date% %time%
echo.

if not exist "G:\livechord\models\beat_refiner.pt" (
    echo *** ERROR: G:\livechord\models\beat_refiner.pt not found.
    echo *** Run train_refiner_2_train.bat first.
    pause
    exit /b 1
)

if not exist "V:\data\models" mkdir "V:\data\models"
if not exist "data\models"    mkdir "data\models"

echo Copying beat_refiner.pt to V:\data\models\ ...
copy /Y "G:\livechord\models\beat_refiner.pt" "V:\data\models\beat_refiner.pt"
if errorlevel 1 goto :fail

echo Copying history.json to V:\data\models\ ...
copy /Y "G:\livechord\models\history.json" "V:\data\models\history.json"
if errorlevel 1 goto :fail

echo Copying beat_refiner.pt to PC-side data\models\ (for git tracking) ...
copy /Y "G:\livechord\models\beat_refiner.pt" "data\models\beat_refiner.pt"
if errorlevel 1 goto :fail

echo.
echo === Step 3 done at %date% %time% ===
echo.
echo Files now at:
echo   V:\data\models\beat_refiner.pt        (NUC runtime can read)
echo   V:\data\models\history.json
echo   data\models\beat_refiner.pt           (PC source tree)
echo.
echo Next: implement backend\ai\beat_refiner_infer.py and wire into pipeline
echo       (Claude: continue Phase 1 integration after reviewing history.json)

pause
endlocal
exit /b 0

:fail
echo.
echo *** COPY FAILED with exit code %errorlevel% ***
echo Likely causes:
echo   - V:\ not mounted on this PC
echo   - File in use by another process
pause
endlocal
exit /b 1
