# 수동 테스트용 실행 스크립트
# 사용법: 이 파일을 PowerShell에서 실행 → 창 2개(라즈베리파이 전송서버, PC 웹서버)가 자동으로 뜨고
#         브라우저로 대시보드가 열립니다.
#
#   powershell -ExecutionPolicy Bypass -File .\start_test.ps1
#
# (그냥 더블클릭하면 "스크립트 실행 정책" 때문에 막힐 수 있어서, 위 명령으로 실행하는 걸 권장합니다.)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "1/3 라즈베리파이 전송 대기 서버(upload_server.py) 창을 엽니다..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @("-NoExit", "-File", (Join-Path $ScriptDir "_pi_run_upload_server.ps1"))

Start-Sleep -Seconds 2

Write-Host "2/3 PC 웹 서버(main.py) 창을 엽니다..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @("-NoExit", "-File", (Join-Path $ScriptDir "_pc_run_server.ps1"))

Start-Sleep -Seconds 3

Write-Host "3/3 브라우저를 엽니다: http://localhost:8000/" -ForegroundColor Cyan
Start-Process "http://localhost:8000/"

Write-Host ""
Write-Host "완료. 새 창 2개가 계속 켜져 있어야 정상 동작합니다." -ForegroundColor Green
Write-Host "라즈베리파이에서 사진을 새로 찍고 싶으면 이어서 capture_on_pi.ps1 을 실행하세요."
Write-Host "테스트 끝나면 stop_test.ps1 을 실행해서 정리하세요."
