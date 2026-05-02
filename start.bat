@echo off

REM Post-beta deployment mode pin. Without this the process falls through to
REM data/settings.json which still carries deployment_mode="beta" from the
REM 2026-04 invite-only beta -- that would 404 every personal-mode endpoint
REM (auto worker, library, backup) and force the front-end into the legacy
REM Beta login UI on LAN. Env var wins over settings.json in config.py.
set LIVECHORD_MODE=public
set LIVECHORD_ADMIN_EMAILS=hiteacherwu@gmail.com

REM Add Windows firewall rule for port 8800 (needs Admin elevation)
netsh advfirewall firewall show rule name="LiveChord Server" >nul 2>&1
if %errorlevel% neq 0 (
    echo Adding firewall rule for port 8800...
    netsh advfirewall firewall add rule name="LiveChord Server" dir=in action=allow protocol=TCP localport=8800 >nul 2>&1
    if %errorlevel% neq 0 (
        echo [WARNING] Failed to add firewall rule. Try running as Administrator.
    ) else (
        echo Firewall rule added.
    )
) else (
    echo Firewall rule already exists.
)

echo ==========================================
echo   LiveChord - Starting...
echo ==========================================
cd /d "%~dp0backend"
python run.py
pause
