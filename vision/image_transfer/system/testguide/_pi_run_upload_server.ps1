$PI_USER = "mobility_vis"
$PI_IP = "192.168.2.28"
$PI_AGENT_DIR = "/home/$PI_USER/image_transfer/pi_agent"

Write-Host "[Pi] upload_server.py 실행 중 - 이 창은 닫지 마세요" -ForegroundColor Yellow
ssh "$PI_USER@$PI_IP" "cd $PI_AGENT_DIR && python3 upload_server.py"
