import os

# 이 서버(VIS 통합 서버) 자체의 실행 주소
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000

# 라즈베리파이 2호기의 전송 트리거 서버 주소
# 결정 사항(design/README.md §8): PC 고정 IP와 마찬가지로 Pi 쪽 주소도 개발 중에는
# 그때그때 확인해서 채워 씀. 실제 배포 시 고정 IP로 교체.
PI_HOST = "192.168.2.28"
PI_TRIGGER_PORT = 8001
PI_TRIGGER_URL = f"http://{PI_HOST}:{PI_TRIGGER_PORT}"
PI_REQUEST_TIMEOUT_SEC = 30.0  # 전송은 동기 대기 방식(라즈베리파이_전환_구현계획.md 참고)이라 넉넉하게

# 라즈베리파이가 이미지를 업로드해서 도착하는 곳
RECEIVED_DIR = os.path.join(os.path.dirname(__file__), "received")

# 파일명 규칙: 20260815_143205_cam01_001.jpg -> 앞 8자리가 날짜
FILENAME_DATE_PREFIX_LEN = 8
