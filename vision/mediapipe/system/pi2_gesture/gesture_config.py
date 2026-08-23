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

# --- PC web_dashboard 경유 (정지/재출발/가속/감속·하트비트 — design/README.md §3-1-1, §3-2) ---
# 2026-08-22: 정지/재출발도 1호기 직접 연결에서 PC 경유로 통합함(§3-1-1 참고).
# 예전 DRIVE_PI_CONTROL_URL/TOKEN(1호기 직접용)은 더 이상 쓰지 않아 제거함.
DASHBOARD_URL = "http://192.168.2.175:8080"  # 대시보드 PC(Seung-Jins-Macbook-3)의 현재 IP
HEARTBEAT_INTERVAL_S = 0.5  # 워치독(1.5초)보다 충분히 짧게 (design/README.md §3-1-4 문제 4)
