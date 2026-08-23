# 라즈베리파이에서 사진을 새로 찍고 싶을 때 실행하는 스크립트
# 사용법: powershell -ExecutionPolicy Bypass -File .\capture_on_pi.ps1
#
# 새 창이 뜨고 라즈베리파이에 접속해서 1초에 한 장씩 사진을 찍습니다.
# 몇 장 찍히면 그 창에서 Ctrl+C 를 눌러 멈추세요 (창은 닫지 말고 그대로 둬도 됩니다).

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "라즈베리파이에서 capture.py를 실행합니다. 몇 장 찍히면 그 창에서 Ctrl+C로 멈추세요." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @("-NoExit", "-File", (Join-Path $ScriptDir "_pi_run_capture.ps1"))
