$PI_USER = "mobility_vis"
$PI_IP = "192.168.2.28"
$PI_AGENT_DIR = "/home/$PI_USER/image_transfer/pi_agent"

Write-Host "[Pi] capture.py 실행 중 - 사진 몇 장 찍히면 Ctrl+C" -ForegroundColor Yellow
ssh "$PI_USER@$PI_IP" "cd $PI_AGENT_DIR && python3 capture.py"
