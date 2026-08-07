$ErrorActionPreference = "Stop"
$logPath = "C:\LiveChord\restart_headless.log"

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "[$stamp] $Message"
}

# Keep runtime mode consistent with current production behavior.
$env:LIVECHORD_MODE = "personal"
$env:LIVECHORD_ADMIN_EMAILS = "livechordcookie@gmail.com"

function Ensure-FirewallRule {
    $ruleName = "LiveChord Server"
    $exists = netsh advfirewall firewall show rule name="$ruleName" 2>$null
    if ($LASTEXITCODE -ne 0) {
        netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow protocol=TCP localport=8800 | Out-Null
    }
}

function Stop-PortListeners {
    param([int[]]$Ports)

    foreach ($port in $Ports) {
        $pids = @(netstat -ano | Select-String ":$port\s+.*LISTENING" | ForEach-Object {
            $parts = ($_ -split "\s+") | Where-Object { $_ -ne "" }
            if ($parts.Count -ge 5) { $parts[-1] }
        } | Sort-Object -Unique)

        foreach ($procId in $pids) {
            if ($procId -match "^\d+$") {
                try {
                    Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop
                } catch {
                    # Ignore already-exited processes.
                }
            }
        }
    }
}

function Resolve-Python {
    $candidates = @(
        "C:\Users\hitea\AppData\Local\Python\bin\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )

    foreach ($p in $candidates) {
        if (Test-Path $p) {
            return $p
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return "py"
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return "python"
    }

    throw "Python executable not found for headless restart."
}

try {
    Write-Log "restart_headless start"
    Ensure-FirewallRule
    Stop-PortListeners -Ports @(8800, 8801, 8802)

    Start-Sleep -Milliseconds 800

    $pythonExe = Resolve-Python
    $backendDir = "C:\LiveChord\backend"
    $args = @("-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8800")

    Write-Log "python=$pythonExe"
    Start-Process -FilePath $pythonExe -ArgumentList $args -WorkingDirectory $backendDir | Out-Null
    Write-Log "restart_headless done"
} catch {
    Write-Log ("ERROR " + $_.Exception.Message)
    exit 1
}
