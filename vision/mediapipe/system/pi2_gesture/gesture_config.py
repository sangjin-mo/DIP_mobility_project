# 라즈베리파이 2호기 — 제스처 인식(gesture_recognizer.py)/상태 머신(gesture_controller.py) 공용 설정
# 값 대부분은 실측 전 TODO — design/README.md 참고

# --- 모델 ---
GESTURE_MODEL_PATH = "models/gesture_recognizer.task"  # https://ai.google.dev/edge/mediapipe 모델 페이지에서 다운로드
GESTURE_CONFIDENCE_THRESHOLD = 0.6  # TODO: 실측으로 조정

# --- 인식 대상 제스처 라벨 (MediaPipe Gesture Recognizer 기본 제공 8종 중 3종) ---
GESTURE_STOP = "Closed_Fist"
GESTURE_ACCELERATE = "Thumb_Up"
GESTURE_DECELERATE = "Thumb_Down"

# --- 디바운스 (프레임 노이즈로 잘못 확정되지 않도록, stop_sign의 DEBOUNCE_N/M과 동일 패턴) ---
GESTURE_DEBOUNCE_N = 3  # 연속 몇 프레임 같은 제스처가 잡히면 확정할지 — TODO: 실측 후 조정
GESTURE_DEBOUNCE_M = 5  # (주먹 해제 판정용) 연속 몇 프레임 미검출이면 해제로 볼지 — TODO: 실측 후 조정

# --- 속도 증감 (design/README.md §3-2-1) ---
SPEED_STEP_MPS = 0.05
SPEED_COOLDOWN_S = 2.0  # 한 번 명령을 보낸 뒤 같은/반대 제스처를 무시하는 시간
MIN_SPEED_MPS = 0.05
MAX_SPEED_MPS = 0.50  # web_dashboard/config.py의 MAX_TARGET_SPEED_MPS와 반드시 일치시킬 것 (design/README.md §3-2-3 문제 6)

# --- 1호기 직접 연결 (정지/재출발 — PC 안 거침, design/README.md §3-1-1) ---
# TODO: 실제 1호기 IP로 교체
DRIVE_PI_CONTROL_URL = "http://192.168.0.51:9000/api/control"
DRIVE_PI_CONTROL_TOKEN = ""  # TODO: 1호기 dashboard_control.py의 ROVER_CONTROL_TOKEN과 동일한 값으로 맞출 것

# --- PC web_dashboard 경유 (가속/감속·하트비트 — design/README.md §3-2) ---
# TODO: 실제 PC IP로 교체
DASHBOARD_URL = "http://192.168.0.10:8080"
HEARTBEAT_INTERVAL_S = 0.5  # 워치독(1.5초)보다 충분히 짧게 (design/README.md §3-1-4 문제 4)
