$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $ScriptDir "..\pc_server")

Write-Host "[PC] main.py 실행 중 - 이 창은 닫지 마세요" -ForegroundColor Yellow
py -m uvicorn main:app --host 0.0.0.0 --port 8000
