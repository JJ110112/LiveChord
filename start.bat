@echo off

REM 以管理員權限加入 Windows 防火牆規則（允許 port 8800）
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
