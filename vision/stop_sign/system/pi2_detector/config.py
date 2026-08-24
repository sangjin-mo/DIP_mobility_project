# 라즈베리파이 2호기 — 정지 표지판 인식(detector.py)/상태 API(state_api.py) 공용 설정
# 값 대부분은 실측 전 TODO — design/README.md §8 "아직 안 정한 것" 참고

# --- 카메라 ---
# 2026-08-23: 실측 결과 이 Pi의 웹캠(C920)은 물리 카메라가 1개뿐이고, index 1은
# 캡처용이 아닌 메타데이터 전용 노드(cv2.VideoCapture(1)도 항상 opened=False)로 확인됨.
# 2026-08-24: 그렇다고 image_transfer(capture.py)와 stop_sign이 둘 다 index 0을 직접
# cv2.VideoCapture로 열면 그 자체로 또 충돌(동시 오픈 미지원, 한쪽만 정상 동작)하므로,
# 카메라는 image_transfer의 capture.py 프로세스 하나만 열고, stop_sign은 그 프로세스가
# 이미 갖고 있는 최신 프레임을 HTTP로 받아오는 방식으로 전환함(이전 "index 0 공유" 안을
# 대체). 실제 측면 전용 카메라가 나중에 배선되면 cv2.VideoCapture(그 인덱스)로 직접 여는
# 방식으로 되돌리면 됨.
CAPTURE_SERVICE_URL = "http://127.0.0.1:8002/latest-frame"
CAPTURE_SERVICE_TIMEOUT_SEC = 2.0

# --- 인식 모델 ---
MODEL_PATH = "yolov8n.pt"  # 사전학습 COCO 가중치, 파인튜닝 없이 사용 (design/README.md §7 검증 결과)
STOP_SIGN_CLASS_ID = 11  # COCO 클래스 목록 기준 "stop sign"
CONFIDENCE_THRESHOLD = 0.5  # TODO: 실측으로 조정 (design/README.md §8)

# Pi 4는 GPU 가속 없이 CPU로만 추론해서 프레임당 처리 시간이 길고, 그게 디바운스(N/M)
# 프레임 수만큼 곱해져서 정지 반응이 느리게 느껴지는 문제가 있었음. 추론 입력 해상도를
# 낮추면(원본 프레임은 그대로 두고 YOLO에 넘길 때만 축소) 정확도 손해를 어느 정도
# 감수하고 속도를 크게 올릴 수 있음 — TODO: 320 기준으로 반응속도/원거리 인식률 실측 후 조정
INFERENCE_IMGSZ = 320  # ultralytics 기본값은 640 — 절반으로 줄이면 추론이 훨씬 빨라짐

# --- 디바운스 (신호가 프레임 노이즈로 깜빡이지 않게, §3) ---
DEBOUNCE_N = 3  # TODO: 연속 탐지 몇 프레임이면 HIGH로 확정할지, 실측 후 조정
DEBOUNCE_M = 5  # TODO: 연속 미탐지 몇 프레임이면 LOW로 확정할지, 실측 후 조정

# --- 쿨다운 (재정지 방지, §3-1) ---
# STOP 요청을 보낸 시점부터 이 시간 동안은 같은 표지판이 계속 잡혀도 재신호를 보내지 않음.
# "정지 → 재출발 → 표지판을 완전히 벗어남"까지 걸리는 시간보다 넉넉해야 함 — 아직 실측 전.
COOLDOWN_SECONDS = 5.0  # TODO: 실측 후 조정

# GPIO 핀 직결은 점퍼케이블 연결이 물리적으로 불가능해져 폐기 — 정지 신호는
# dashboard_client.stop()을 통해 PC web_dashboard의 "■ 정지" 버튼과 동일한 경로로 전송함
# (../../../mediapipe/design/README.md §3-1-1 참고. URL 설정은 그쪽 gesture_config.py의 DASHBOARD_URL)

# --- 제스처(손동작) 인식 on/off (mediapipe/design/README.md §4-5 참고) ---
# 이 Pi(Cortex-A72, AES 명령어 미지원)에서는 공식 mediapipe 패키지가
# GestureRecognizer.create_from_options() 호출 시 SIGILL로 크래시함(자체 우회
# 파이프라인은 아직 프로토타입 단계). 그동안은 False로 두고 표지판 인식만 배포.
# GestureController(정지 사유 관리)는 이 플래그와 무관하게 항상 사용함 — 표지판
# 정지 요청도 그 클래스를 거치기 때문(§4-2).
GESTURE_RECOGNITION_ENABLED = False

# --- 주행 상태 판단 (§3-2) ---
# 2026-08-22: push를 기다리는 대신, PC web_dashboard의 GET /api/control/status를
# 직접 폴링하는 방식으로 전환 (아무도 /vehicle-state를 호출해주지 않는 문제 해결).
# URL은 features/mediapipe/system/pi2_gesture/gesture_config.py의 DASHBOARD_URL 재사용.
DRIVING_STATE_POLL_INTERVAL_S = 1.0  # TODO: 반응성 vs 트래픽 고려해 실측 후 조정

# 아래 push용 엔드포인트는 폴링과 별개로 수동 테스트 편의를 위해 유지 (testguide 참고).
# PC 제어 서버가 POST {API_HOST}:{API_PORT}/vehicle-state 로 주행 상태를 푸시할 수도 있음
API_HOST = "0.0.0.0"
API_PORT = 8010
# TODO: 허용 IP를 PC 고정 IP로 제한할지 여부 — image_transfer의 TRIGGER_ALLOWED_HOST와 동일한 논의 필요 (§8)
