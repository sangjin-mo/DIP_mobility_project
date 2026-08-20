import os

# 카메라
CAMERA_INDEX = 0
CAMERA_ID = "cam01"
CAPTURE_INTERVAL_SEC = 1.0

# 저장 경로 (라즈베리파이 로컬)
BASE_DIR = os.path.join(os.path.dirname(__file__), "images")

# 저장 공간 관리
MIN_FREE_DISK_MB = 500  # 여유 공간이 이 아래로 떨어지면 오래된 폴더부터 삭제
DISK_CHECK_INTERVAL_SEC = 30  # 매 프레임마다 확인하지 않고 이 간격으로만 확인

# 촬영 실패 재시도
MAX_CONSECUTIVE_FAILURES = 5  # 이 횟수 연속 실패하면 카메라 재연결 시도
RECONNECT_RETRY_DELAY_SEC = 3.0  # 재연결 재시도 간격

# 전송 대상 (PC 수신 서버)
# 결정 사항(design/README.md §8): PC 고정 IP는 아직 미정 — 실제 PC IP로 바꿔서 사용
SERVER_HOST = "192.168.2.XXX"
SERVER_PORT = 8000
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
UPLOAD_TIMEOUT_SEC = 3.0

# 전송 상태 기록 (성공한 파일은 다시 안 보내도록, 실패한 파일은 다음 요청 때 재시도)
UPLOAD_STATUS_FILE = os.path.join(os.path.dirname(__file__), "upload_status.json")

# 전송 트리거 서버 (라즈베리파이 쪽에서 실행, PC의 요청을 받아 업로드를 시작함)
TRIGGER_PORT = 8001
# 결정 사항(design/README.md §8): PC 고정 IP 확정 전까지 None 유지 (아무 IP나 요청 가능)
# PC 고정 IP가 정해지면 채워서 그 주소의 요청만 허용 (예: "192.168.0.50")
TRIGGER_ALLOWED_HOST = None
