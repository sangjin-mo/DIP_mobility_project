# Starts this PC as the FarmRover central server: ai_report ingest,
# the vision team's pc_server, and web_dashboard together. Opens a
# Windows Defender Firewall rule for the duration and removes it again
# on exit.
#
# Usage: .\scripts\start_central_server.ps1
# Run from an elevated ("Run as Administrator") PowerShell to get the
# firewall step; without elevation everything else still runs, but you'll
# need to allow the port yourself (see the warning this script prints).

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "No .venv found at repo root. Create one first:`n  python -m venv .venv; .\.venv\Scripts\pip.exe install -e ."
    exit 1
}

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Read individual keys out of .env by regex instead of dot-sourcing it --
# some values (e.g. DASHBOARD_WEATHER_LOCATION_LABEL) contain unquoted
# spaces and are not meant to be evaluated as script/expression syntax.
function Get-EnvValue([string]$Key) {
    $envFile = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $envFile)) { return "" }
    $line = Select-String -Path $envFile -Pattern "^$Key=" | Select-Object -Last 1
    if (-not $line) { return "" }
    return $line.Line.Substring($Key.Length + 1)
}

$DashboardRoverControlUrl = Get-EnvValue "DASHBOARD_ROVER_CONTROL_URL"
$DashboardRoverControlToken = Get-EnvValue "DASHBOARD_ROVER_CONTROL_TOKEN"
$DashboardRoverStatusUrl = Get-EnvValue "DASHBOARD_ROVER_STATUS_URL"
$DrivePiSshHost = Get-EnvValue "DRIVE_PI_SSH_HOST"
$DrivePiSshUser = Get-EnvValue "DRIVE_PI_SSH_USER"
if ([string]::IsNullOrWhiteSpace($DrivePiSshUser)) { $DrivePiSshUser = "pi" }

$FirewallRuleName = "FarmRover central server (venv python)"
$FirewallOpened = $false
$ChildProcesses = @()

function Test-IsElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Open-Firewall {
    if (-not (Test-IsElevated)) {
        Write-Warning "Not running as Administrator -- skipping the firewall rule."
        Write-Warning "Re-run this script 'as Administrator' to have it open/close the port automatically,"
        Write-Warning "or allow $VenvPython through Windows Defender Firewall yourself."
        return
    }
    if (Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue) {
        Write-Host "Firewall rule '$FirewallRuleName' already exists."
        $script:FirewallOpened = $true
        return
    }
    Write-Host "Opening firewall for $VenvPython ..."
    New-NetFirewallRule -DisplayName $FirewallRuleName -Direction Inbound -Program $VenvPython -Action Allow -Profile Any | Out-Null
    $script:FirewallOpened = $true
}

function Close-Firewall {
    if ($FirewallOpened) {
        Write-Host "Closing firewall rule '$FirewallRuleName' ..."
        Remove-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue | Out-Null
    }
}

function Stop-AllChildren {
    Write-Host ""
    Write-Host "Stopping central server..."
    foreach ($p in $ChildProcesses) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Close-Firewall
    Write-Host "Stopped."
}

function Ensure-PcServerDeps {
    $req = Join-Path $RepoRoot "vision\image_transfer\system\pc_server\requirements.txt"
    & $VenvPython -c "import fastapi, uvicorn, jinja2, requests, multipart" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing vision pc_server dependencies into .venv ..."
        & $VenvPython -m pip install -q -r $req
    }
}

function Test-DrivePi {
    $statusUrl = $DashboardRoverStatusUrl
    if ([string]::IsNullOrWhiteSpace($statusUrl) -and -not [string]::IsNullOrWhiteSpace($DashboardRoverControlUrl)) {
        $statusUrl = $DashboardRoverControlUrl -replace "/api/control$", "/api/status"
    }

    if (-not [string]::IsNullOrWhiteSpace($DrivePiSshHost)) {
        Write-Host "Attempting best-effort remote start of drive control on $DrivePiSshHost ..."
        $remoteCmd = "cd ~/mycar && DASHBOARD_CONTROL_TOKEN='$DashboardRoverControlToken' nohup python manage.py drive --model=models/mypilot.h5 >drive.log 2>&1 & disown"
        # ssh.exe is a native command -- it reports failure via $LASTEXITCODE, not
        # a terminating exception, so a try/catch here would never fire.
        ssh -o ConnectTimeout=5 -o BatchMode=yes "$DrivePiSshUser@$DrivePiSshHost" $remoteCmd
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  (SSH remote start failed, or drive control was already running -- continuing)"
        }
        Start-Sleep -Seconds 2
    }

    if ([string]::IsNullOrWhiteSpace($statusUrl)) {
        Write-Host "DASHBOARD_ROVER_CONTROL_URL not set -- skipping drive Pi reachability check."
        return
    }

    Write-Host "Checking drive Pi at $statusUrl ..."
    try {
        Invoke-RestMethod -Uri $statusUrl -TimeoutSec 3 | Out-Null
        Write-Host "  Drive Pi control server is reachable."
    } catch {
        Write-Warning "  Drive Pi control server did NOT respond at $statusUrl."
        Write-Warning "  Drive controls will show as unreachable in the dashboard until it is started."
    }
}

try {
    Open-Firewall
    Ensure-PcServerDeps

    Write-Host "Starting ai_report ingest (UDP 9100 / HTTP 9101) ..."
    $aiReportLog = Join-Path $LogDir "ai_report.log"
    $ai = Start-Process -FilePath $VenvPython -ArgumentList "-m", "ai_report.cli", "serve" `
        -RedirectStandardOutput $aiReportLog -RedirectStandardError "$aiReportLog.err" `
        -NoNewWindow -PassThru
    $ChildProcesses += $ai

    Write-Host "Starting vision pc_server (port 8000) ..."
    $pcServerDir = Join-Path $RepoRoot "vision\image_transfer\system\pc_server"
    $pcServerLog = Join-Path $LogDir "pc_server.log"
    $pcs = Start-Process -FilePath $VenvPython -ArgumentList "main.py" -WorkingDirectory $pcServerDir `
        -RedirectStandardOutput $pcServerLog -RedirectStandardError "$pcServerLog.err" `
        -NoNewWindow -PassThru
    $ChildProcesses += $pcs

    Start-Sleep -Seconds 1
    Test-DrivePi

    $LocalHostname = $env:COMPUTERNAME
    Write-Host ""
    Write-Host "Central server starting. Dashboard will be reachable at:"
    Write-Host "  http://$LocalHostname.local:8080   (needs Bonjour/mDNS installed on this PC)"
    Write-Host "  http://$LocalHostname:8080          (works on Windows networks without mDNS)"
    Write-Host "Logs: $aiReportLog, $pcServerLog"
    Write-Host "Press Ctrl+C to stop everything and remove the firewall rule."
    Write-Host ""

    & $VenvPython -m web_dashboard
}
finally {
    Stop-AllChildren
}
