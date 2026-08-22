$PI_USER = "mobility_vis"
$PI_IP = "192.168.2.28"
$PI_DETECTOR_DIR = "/home/$PI_USER/stop_sign/pi2_detector"

Write-Host "[Pi 2호기] detector.py 실행 중 - Ctrl+C로 종료" -ForegroundColor Yellow
ssh "$PI_USER@$PI_IP" "cd $PI_DETECTOR_DIR && python3 detector.py"
