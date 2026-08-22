# 라즈베리파이 2호기 — 정지 표지판 인식(detector.py)/상태 API(state_api.py) 공용 설정
# 값 대부분은 실측 전 TODO — design/README.md §8 "아직 안 정한 것" 참고

# --- 카메라 ---
SIDE_CAMERA_INDEX = 1  # TODO: 실제 측면 카메라의 /dev/videoN 인덱스로 교체 (작물 카메라와 다른 장치)
# 2026-08-22: 작물 카메라(index 0)를 임시로 빌려 2호기 실기에서 GPIO 하드웨어 검증 완료 (design/README.md §7 참고)

# --- 인식 모델 ---
MODEL_PATH = "yolov8n.pt"  # 사전학습 COCO 가중치, 파인튜닝 없이 사용 (design/README.md §7 검증 결과)
STOP_SIGN_CLASS_ID = 11  # COCO 클래스 목록 기준 "stop sign"
CONFIDENCE_THRESHOLD = 0.5  # TODO: 실측으로 조정 (design/README.md §8)

# --- 디바운스 (신호가 프레임 노이즈로 깜빡이지 않게, §3) ---
DEBOUNCE_N = 3  # TODO: 연속 탐지 몇 프레임이면 HIGH로 확정할지, 실측 후 조정
DEBOUNCE_M = 5  # TODO: 연속 미탐지 몇 프레임이면 LOW로 확정할지, 실측 후 조정

# --- 쿨다운 (재정지 방지, §3-1) ---
# HIGH를 보낸 시점부터 이 시간 동안은 같은 표지판이 계속 잡혀도 재신호를 보내지 않음.
# "정지 → 재출발 → 표지판을 완전히 벗어남"까지 걸리는 시간보다 넉넉해야 함 — 아직 실측 전.
COOLDOWN_SECONDS = 5.0  # TODO: 실측 후 조정

# --- GPIO (§3) ---
GPIO_OUT_PIN = 17  # TODO: 실제 배선 시 핀 번호 확정 (BCM 번호 기준, gpiozero 사용)

# --- 주행 상태 API (§3-2) ---
# PC 제어 서버가 POST {API_HOST}:{API_PORT}/vehicle-state 로 주행 상태를 푸시
API_HOST = "0.0.0.0"
API_PORT = 8010
# TODO: 허용 IP를 PC 고정 IP로 제한할지 여부 — image_transfer의 TRIGGER_ALLOWED_HOST와 동일한 논의 필요 (§8)
