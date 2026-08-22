param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("true", "false")]
    [string]$Driving
)

$PI_IP = "192.168.2.28"
$PORT = 8010

$body = @{ driving = [System.Convert]::ToBoolean($Driving) } | ConvertTo-Json

Write-Host "[PC] POST http://${PI_IP}:${PORT}/vehicle-state  driving=$Driving" -ForegroundColor Yellow
Invoke-RestMethod -Method Post -Uri "http://${PI_IP}:${PORT}/vehicle-state" -Body $body -ContentType "application/json"
