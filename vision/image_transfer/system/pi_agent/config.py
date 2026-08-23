import os

# 카메라
CAMERA_INDEX = 0
CAMERA_ID = "cam01"
CAPTURE_INTERVAL_SEC = 1.0  # 시작 시 기본값. 실행 중엔 POST /set-interval로 변경 가능
MIN_CAPTURE_INTERVAL_SEC = 0.2  # 이보다 짧게는 설정 못 하게 막음 (CPU/디스크 부담 보호)

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

# 차량 구동 상태 확인 (통합 대시보드의 읽기 전용 상태 API, web_dashboard 소유 — 여기서는 조회만 함)
# state가 "RUNNING"일 때만 촬영·저장하고, 그 외("STOPPED" 등)에는 저장을 건너뜀.
# 같은 PC에서 통합 대시보드(web_dashboard)가 8080 포트로 떠 있다고 가정 (SERVER_HOST 재사용)
CONTROL_STATUS_URL = f"http://{SERVER_HOST}:8080/api/control/status"
CONTROL_STATUS_POLL_SEC = 2.0  # 상태를 얼마나 자주 다시 확인할지
CONTROL_STATUS_TIMEOUT_SEC = 2.0
# 상태 API를 못 읽었을 때(연결 끊김, 형식 오류 등) 촬영을 계속할지 여부.
# False면 "확인 안 되면 정지로 간주"(안전 우선, 저장공간 낭비 방지) — 기본값 권장
FAIL_OPEN_WHEN_STATUS_UNKNOWN = False

# 수동 단발 촬영 API (capture.py 자신이 띄움, 카메라를 이미 잡고 있는 프로세스라서
# upload_server.py가 아니라 여기서 직접 서비스함). 자동 주기 촬영의 on/off와 무관하게
# 항상 동작 — "촬영" 버튼은 정지 상태에서도 눌리는 수동 오버라이드이기 때문.
CAPTURE_CONTROL_PORT = 8002

# 전송 상태 기록 (성공한 파일은 다시 안 보내도록, 실패한 파일은 다음 요청 때 재시도)
UPLOAD_STATUS_FILE = os.path.join(os.path.dirname(__file__), "upload_status.json")

# 전송 트리거 서버 (라즈베리파이 쪽에서 실행, PC의 요청을 받아 업로드를 시작함)
TRIGGER_PORT = 8001
# 결정 사항(design/README.md §8): PC 고정 IP 확정 전까지 None 유지 (아무 IP나 요청 가능)
# PC 고정 IP가 정해지면 채워서 그 주소의 요청만 허용 (예: "192.168.0.50")
TRIGGER_ALLOWED_HOST = None
