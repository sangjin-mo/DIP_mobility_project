# 테스트 종료 정리 스크립트
# 사용법: powershell -ExecutionPolicy Bypass -File .\stop_test.ps1
#
# PC 웹 서버(main.py)와 라즈베리파이의 upload_server.py / capture.py를 전부 종료합니다.
# start_test.ps1로 띄운 새 창들은 프로세스가 죽으면서 자동으로 닫히지 않을 수 있으니,
# 남아있는 창은 손으로 닫아주세요.

$PI_USER = "mobility_vis"
$PI_IP = "192.168.2.28"   # 라즈베리파이 IP가 바뀌었으면 여기만 고치면 됩니다

Write-Host "PC 웹 서버 종료 중..." -ForegroundColor Cyan
$pcProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*uvicorn*main:app*' }
if ($pcProcs) {
    foreach ($p in $pcProcs) {
        Stop-Process -Id $p.ProcessId -Force
        Write-Host "  PC 서버 PID $($p.ProcessId) 종료됨"
    }
} else {
    Write-Host "  실행 중인 PC 서버가 없습니다."
}

Write-Host "라즈베리파이 쪽 서버 종료 중..." -ForegroundColor Cyan
ssh "$PI_USER@$PI_IP" "pkill -f upload_server.py; pkill -f capture.py" 2>$null
Write-Host "  요청 완료 (라즈베리파이가 꺼져 있으면 이 단계는 그냥 무시됩니다)"

Write-Host ""
Write-Host "정리 완료. 남아있는 SSH/서버 창이 있으면 직접 닫아주세요." -ForegroundColor Green
