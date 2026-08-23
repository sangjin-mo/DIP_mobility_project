# mediapipe 기능 도입 검토

> 작성일: 2026-08-22
> 상태: 검토 중 (아직 채택 여부 미확정)
> 위치: `features/mediapipe/design/`

---

## 1. 검토 배경

기존 파이프라인(`features/image_analysis/`, `features/image_transfer/`)에 MediaPipe 기능 추가 여부를 검토한다.

## 2. 참고 저장소 조사 결과

[google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe) 확인 결과:

- **Gesture Recognizer** 솔루션이 기본 제스처 8종(Unknown, Closed_Fist, Open_Palm, Pointing_Up, Thumb_Down, Thumb_Up, Victory, ILoveYou)을 사전 학습된 상태로 제공 → 우리가 검토 중인 **주먹(Closed_Fist)/엄지 위(Thumb_Up)/엄지 아래(Thumb_Down)** 3가지 모두 별도 커스텀 학습 없이 바로 사용 가능
- Python API 지원, `IMAGE`/`VIDEO`/`LIVE_STREAM` 3가지 실행 모드 제공 — 웹캠 등 실시간 스트림 입력은 `LIVE_STREAM` 모드 + 비동기 결과 리스너로 처리
- 라이선스: Apache-2.0 (기존 검토했던 YOLO-World의 AGPL-3.0보다 사용 제약이 적음)
- 커스텀 제스처가 필요해지면 별도 커스터마이제이션 튜토리얼로 확장 가능 (지금 단계에서는 불필요)
- → **결론(잠정)**: 정지/가속/감속 3개 제스처만 필요한 현재 요구사항은 기본 제공 모델로 충분히 커버되며, 별도 데이터셋 라벨링·학습 없이 도입 가능해 보임. 실측(정확도, GTX 1650 Ti 4GB에서의 지연시간)은 아직 미검증.

## 3. 검토 중인 기능 — 손동작(Hand Gesture) 기반 모빌리티 제어

MediaPipe **Gesture Recognizer** 솔루션(기본 제공 8종 제스처 중 3종 사용)을 이용해 아래 3가지 손동작을 인식, 모빌리티(주행) 제어 명령으로 매핑하는 방안을 검토 중.

| 동작 | 손 모양 | MediaPipe 제스처 라벨 | 매핑 명령 |
|---|---|---|---|
| 정지 | 주먹 | `Closed_Fist` | STOP |
| 가속 | 따봉(엄지 위) | `Thumb_Up` | ACCELERATE |
| 감속 | 역따봉(엄지 아래) | `Thumb_Down` | DECELERATE |

### 3-1. 정지(주먹) — GPIO 배선 불가로 웹(HTTP) 연동으로 전환 검토

**상황 변경**: 원래 `features/stop_sign/`과 동일하게 "2호기 GPIO OUT → 1호기 GPIO IN" 점퍼케이블 직결 방식을 그대로 쓰려 했으나, **지금 물리적으로 두 라즈베리파이 사이에 점퍼케이블 연결이 불가능한 상황**. 아래처럼 GPIO를 걷어내고 웹(HTTP) 경로로 대체하는 방안을 검토.

#### 3-1-1. 결론부터 — 이미 검증된 패턴이 `drive_ver3`에 있음, 재발명 불필요

`drive_ver2/`, `drive_ver3/`에 **stop_sign과 별개로 이미 구현된** 비전-Pi 정지 클라이언트가 있음:

- `drive_ver3/vision_stop_client.py` — `send_stop_sign_stop(control_url, token)`: 1호기 제어 에이전트(`dashboard_control.py`)에 `{"command": "STOP", ...}`를 **인증 토큰과 함께 직접** 보내는 함수. docstring: "Latch the drive Pi in STOPPED state" — 한 번 보내면 1호기가 STOPPED로 래치되고, 재출발은 별도 START 필요(§3-1-2와 동일 전제)
- `drive_ver3/vision_stop_detector.py:44-67` — 표지판 감지 시 **GPIO 펄스(있으면)와 HTTP STOP을 동시에** 보냄. `--no-gpio` 플래그로 GPIO 없이 HTTP만 쓰는 모드도 이미 지원(`gpio_sender = None if args.no_gpio else ...`) — **지금 이 케이블 없는 상황을 이미 상정하고 만들어진 코드**
- `drive_ver3/vision_gpio.py` — GPIO 있을 때 0.2초 HIGH 펄스만 보내는 방식(구 stop_sign의 "지속 HIGH=정지 유지" 방식과 다름, 엣지 트리거 1회성)

**중요한 정정**: 이 파일들은 전부 **2호기(비전 Pi) → 1호기 직접** 패턴이고 PC를 거치지 않음. 앞서 제가 "PC 경유(경유형)"를 권장했던 건 가감속과 채널을 통일하자는 아키텍처적 이유였는데, **정지는 안전 요구사항이 걸린 별개 기능이라 가감속과 같은 경로일 필요가 없고, PC가 꺼져 있어도 정지는 되어야 한다는 게 더 중요한 제약**이었음을 놓쳤음. 아래처럼 정정:

| 경로 | 방식 | 채택 여부 |
|---|---|---|
| ~~경유형 (PC 거침)~~ | ~~2호기 → PC `web_dashboard` → 1호기~~ | **철회** — PC가 꺼지거나 응답 없으면 정지가 안 됨. 안전 기능에 부적합 |
| **직결형 (채택)** | 2호기(제스처 프로세스도 동일 위치에서 실행 시) → `vision_stop_client.py`로 **1호기에 직접** 인증된 STOP | `drive_ver3`에 이미 구현·존재. `Closed_Fist` 인식 시 `send_stop_sign_stop()` 그대로 호출. (주먹 해제 시 자동 재출발은 요청 범위 밖이라 제거함 — §4-2 구현 현황 참고. §3-1-2의 "정지 전 속도 복원" 로직은 표지판(STOP_SIGN) 해제 시 자동 재출발에만 적용됨) |

가속/감속(§3-2)은 지연에 덜 민감하고 안전 필수 기능이 아니므로 PC 경유(A안) 그대로 유지 — **정지만 2호기→1호기 직결, 가감속은 PC 경유**로 채널을 분리하는 것으로 정정.

**임시방편이라는 점을 명시**: 이 HTTP 기반 정지는 와이파이·IP 할당·공유기 상태에 의존하므로, "로봇이 뭔가에 부딪히기 전에 반드시 멈춰야 한다"는 안전 요구사항의 최종 보증책으로 삼기엔 근본적 한계가 있음(전선은 물리적으로 끊기지 않는 한 신호가 안 갈 수 없지만, 와이파이는 그 순간 하필 안 될 수 있음). `--no-gpio`로 지금은 HTTP만 쓰되, **점퍼케이블을 구하는 대로 GPIO를 다시 켜는 것을 전제**로 진행 — `vision_stop_detector.py`는 플래그 하나(`--no-gpio` 제거)로 그 전환이 이미 가능하도록 만들어져 있음.

#### 3-1-2. 재개 시 속도 복원 — 놓치기 쉬운 디테일 (표지판 해제 시에만 해당)

`POST /api/control/stop`이 호출되면 `dashboard_control.py:136-139`에서 `target_speed_mps`를 **무조건 0.0으로 초기화**함. 재출발할 때 `target_speed_mps`를 안 실어 보내면 `app.py:135`의 `speed = request.target_speed_mps or web_config.DEFAULT_TARGET_SPEED_MPS`에 의해 **정지 전 속도가 아니라 기본값(0.25 m/s)으로 재출발**해버림. 따라서 자동 재출발을 하는 쪽(표지판 해제)은 정지시키기 직전의 `target_speed_mps`를 자체적으로 기억해뒀다가, 재출발(`START`) 요청에 그 값을 그대로 실어 보내야 함.

(주먹은 §4-2에서 정리했듯 애초에 자동 재출발을 하지 않으므로 이 문단은 더 이상 주먹에는 해당하지 않음 — 표지판 해제 시 재출발 로직에만 적용됨)

#### 3-1-3. 디바운스/쿨다운은 유지, 쿨다운 적용 여부는 여전히 미정

- 디바운스(연속 N프레임 확인 후 정지/해제 전환)는 통신 방식과 무관하게 동일하게 적용
- **미정: 쿨다운(lockout) 적용 여부** — stop_sign은 재출발 직후 표지판이 계속 화면에 남아 즉시 재정지되는 문제 때문에 고정 시간 쿨다운을 뒀음(`config.py:20` `COOLDOWN_SECONDS = 5.0`). 손동작은 주먹을 풀면 바로 신호가 사라지므로 같은 문제가 그대로 재현되는지는 "재출발 직후에도 손이 계속 주먹 상태로 프레임에 남아있는 상황이 실제로 있는지"에 달려 있음 — 아직 미정

### 3-1-보충. "차체가 구동 중인지" 0/1 데이터를 웹에서 받을 수 있는가 — 가능함, 이미 있음

두 가지 기존 소스 중 하나를 그대로 쓰면 됨:

1. **PC `web_dashboard`의 `GET /api/control/status`** (`app.py:151-158`) — 응답의 `state` 필드가 `"RUNNING"` / `"STOPPED"` / `"EMERGENCY"`. `state == "RUNNING"` → 1, 그 외 → 0으로 매핑하면 그대로 원하는 0/1 신호.
2. **1호기 에이전트의 `GET /api/status`** (`drive_ver2/dashboard_control.py:179-181`) — 위와 같은 정보를 PC를 거치지 않고 1호기에서 직접 받는 버전.
3. (참고) `features/stop_sign/system/pi2_detector/state_api.py`가 이미 정확히 이 목적(§3-2 자원 절약, "주행 중이 아니면 YOLO 추론 생략")으로 **PC → 2호기 방향 푸시**를 구현해 둠 — 다만 이건 "2호기가 값을 받는" push 모델이고, 지금 질문한 "웹에서 값을 받아온다(pull)"와는 방향이 반대. 제스처 프로세스가 2호기에서 돈다면 이 기존 `vehicle_state`를 그대로 재사용(추가 폴링 없이 이미 갱신되고 있는 로컬 플래그를 읽기만 하면 됨)해도 되고, 새로 PC의 `/api/control/status`를 폴링(pull)하는 방식으로 가도 됨 — 전자가 이미 구현돼 있어 더 적은 변경.

**권장**: 제스처 인식이 2호기에서 돈다면 **기존 `state_api.py`의 `vehicle_state.is_driving()`을 그대로 재사용**(자원 절약용 추론 on/off 판단에 그대로 씀). 반면 "재개 시 어떤 속도로 복귀할지"(3-1-2) 같은 값까지 필요하면 `vehicle_state`엔 속도 정보가 없으므로(bool만 있음) PC의 `/api/control/status`를 별도로 호출해 `target_speed_mps`를 받아와야 함.

### 3-1-4. 앞서 지적한 6가지 문제 — 개선 방향

§3-2-3에서 지적한 문제들에 대한 구체적 개선안:

1. **사람 수동 조작 vs 제스처 자동 조작 충돌(TOCTOU)** → 서버 쪽에 "마지막 조작 주체" 구분이 없으므로, 완전히 막으려면 서버 수정이 필요하지만 지금 범위에서는 **운영 규칙으로 해결 권장**: 제스처 모드가 켜져 있는 동안은 브라우저 슬라이더를 조작하지 않는다는 전제를 두거나, 대시보드에 "제스처 모드 ON일 때 슬라이더 disable" 정도의 UI 안전장치만 추가(`dashboard.js:42`의 `setControlButtons()`에 조건 하나 추가하는 수준으로 가능).
2. **속도 하한에서 요청 실패** → 서버에 의존하지 말고 **제스처 클라이언트가 전송 전에 직접 클램프**: `new_speed = round(min(MAX, max(MIN, current ± 0.05)), 2)`. `new_speed <= 0`이 되는 경우엔 `/api/control/start` 대신 `/api/control/stop`을 보내도록 분기.
3. **부동소수점 누적 오차** → 위와 동일하게 매 스텝 `round(x, 2)`로 고정하면 해결.
4. **하트비트(1.5초) < 쿨타임(2초)** → 제스처 클라이언트가 **속도 스텝 명령과 별개로** 0.5~1초 주기 타이머를 두고 `/api/control/heartbeat`를 계속 호출하도록 구현(브라우저 대시보드의 500ms 하트비트 루프와 동일한 역할을 제스처 프로세스도 스스로 수행). 이렇게 하면 브라우저 탭이 닫혀 있어도 안전.
5. **GPIO 정지 채널과 HTTP 가감속 채널의 상태 불일치** → (정정) 정지(2호기→1호기 직결, §3-1-1)와 가감속(PC 경유, §3-2)이 여전히 서로 다른 두 채널이라는 점은 남음. 다만 둘 다 최종적으로 1호기의 같은 `dashboard_control.py` 상태(`RUNNING`/`STOPPED`, `target_speed_mps`)를 갱신하므로, 어느 경로로 오든 1호기 쪽 상태는 항상 하나로 수렴함 — "PC 쪽 상태와 GPIO 쪽 상태가 따로 논다"는 원래 우려는 정지를 GPIO 대신 같은 HTTP 인터페이스(`POST /api/control`)로 보내는 것만으로 해소됨.
6. **최대 속도 설정 중복(PC/Pi)** → 근본 해결은 설정을 한 곳(예: PC가 시작 시 1호기의 `/api/status` 혹은 별도 `/api/capabilities`로 최대 속도를 조회해 캐싱)으로 합치는 것. 지금 범위에서 급하지 않다면 두 값을 배포 체크리스트에 명시해 수동 동기화만 우선 적용해도 됨.

### 3-2. 가속(따봉)/감속(역따봉) — A안 확정: `DIP_mobility_project/web_dashboard` 연동

가속/감속은 **A안(웹 대시보드 API 연동)** 으로 확정. `DIP_mobility_project` 저장소의 아래 6개 파일을 확인해 실제 연동 지점과 잠재 문제를 정리함.

#### 3-2-1. 속도 증감 단위 및 쿨타임 (제안, 유지)

- **증감 단위**: 제스처 1회 인식당 목표 속도를 **±0.05 m/s**씩 변경 (따봉 1회 = +0.05 m/s, 역따봉 1회 = -0.05 m/s) — "한 번 인식 → 한 번 명령"의 이산적(discrete) 스텝 방식
- **쿨타임**: 한 번 인식해 명령을 보낸 뒤 **2초** 동안은 같은(혹은 반대) 제스처가 계속 잡혀도 추가 명령을 보내지 않음
- 2초는 초기 제안값이며, 실측(반응성 vs 오중복 방지) 후 조정 필요

#### 3-2-2. 기존 대시보드 코드 조사 결과 — 다행히 "실행 중 속도 변경"이 이미 있음

처음에는 새 API 엔드포인트가 필요할 거라 예상했으나, 코드를 보니 **이미 있는 메커니즘으로 충분**함.

- `web_dashboard/templates/dashboard.html:95-98` — 목표 속도 슬라이더(`#target-speed`, 0.05~0.50, step 0.05)
- `web_dashboard/static/dashboard.js:530-532` — 슬라이더를 조작(`change` 이벤트)하면 `roverState === "RUNNING"`일 때 **`sendControl("start")`를 다시 호출**함
- `web_dashboard/static/dashboard.js:325-351` (`sendControl`) — `command === "start"`면 현재 `#target-speed` 슬라이더 값을 `target_speed_mps`로 담아 `POST /api/control/start` 호출
- `web_dashboard/app.py:133-141` (`start_rover`) — RUNNING 여부와 무관하게 **항상 새 `target_speed_mps`로 갱신**. "이미 달리는 중엔 못 바꾼다" 같은 제약이 없음
- `drive_ver2/dashboard_control.py:114-135` (`apply_command`, START 분기) — 현재 상태를 확인하지 않고 그냥 `self._state = "RUNNING"; self._target_speed_mps = float(speed)`로 덮어씀

즉 **제스처 클라이언트도 "START 명령을 새 목표 속도로 재호출"하는 것만으로 가속/감속 구현 가능** — 새 명령 타입이나 새 엔드포인트를 추가할 필요가 없음. 제스처 클라이언트는 Pi 제어 에이전트(`drive_ver2/dashboard_control.py`)를 직접 부르지 말고, 토큰·상한 체크가 캡슐화된 **PC의 `web_dashboard` (`app.py`)를 거쳐서** `/api/control/start`, `/api/control/status`를 호출해야 함 (`config.py:35-38`의 `ROVER_CONTROL_URL`/`ROVER_CONTROL_TOKEN`이 이미 여기 있음).

#### 3-2-3. 연동 시 실제로 문제가 될 수 있는 지점

1. **읽고-계산해서-쓰는 구조라 경쟁 상태(TOCTOU) 가능** — 이 방식은 절대값 지정(`target_speed_mps`)이라, 제스처 클라이언트가 ±0.05를 계산하려면 먼저 `GET /api/control/status`(`app.py:151-158`)로 현재 속도를 읽어야 함. 조회 시점과 재전송(`POST start`) 시점 사이에 **사람이 대시보드 슬라이더를 직접 조작하면 그 변경이 덮어써질 수 있음**. `dashboard.js`나 `dashboard_control.py` 어디에도 "누가 마지막으로 조작했는지" 구분이 없어(둘 다 그냥 `START`), 사람 수동 조작과 제스처 자동 조작이 서로 모른 채 충돌 가능. 두 입력 주체가 동시에 존재할 계획이면 우선순위 규칙(예: 사람이 슬라이더를 만지면 N초간 제스처 입력 무시)이 필요.

2. **속도 하한 근처에서 요청이 부드럽게 처리되지 않음** — 검증이 세 군데에 분산돼 있음:
   - `app.py:44` `StartRequest.target_speed_mps: float | None = Field(default=None, gt=0, le=1.0)` — **0 이하 값은 pydantic이 바로 422로 거부**. 최소 속도(0.05)에서 역따봉을 한 번 더 인식하면 계산값이 0.00이 되어 **요청 자체가 실패**함. 서버가 알아서 0으로 클램프하거나 STOP으로 전환해주지 않음 — 제스처 클라이언트가 하한을 직접 클램프하거나, 0 이하가 되면 `/api/control/stop`으로 바꿔 보내는 로직을 넣어야 함.
   - `app.py:136-140`은 **상한**(`MAX_TARGET_SPEED_MPS`, `config.py:41` 기본 0.50)만 별도 체크.
   - `drive_ver2/dashboard_control.py:126-132`에서 `0 < speed <= max_speed_mps`를 **세 번째로 재검증** — 여기서 걸리면 `ControlCommandError`로 이어져 최종적으로 "차량이 명령을 승인하지 않았습니다" 에러 문자열(`control_service.py:100-102`)이 내려옴. 제스처 쪽에서 이 사람용 에러 메시지를 그대로 노출할지, 별도로 잡아 무시할지 정해야 함.

3. **부동소수점 누적 오차** — 0.05를 여러 번 더하고 빼면 이진 부동소수점 특성상 `0.34999999999999997` 같은 값이 쌓일 수 있음. 매 스텝 `round(value, 2)`로 고정하지 않으면 위 2번의 경계 비교(`<=`, `>`)에서 예상 못한 거부가 발생할 수 있음.

4. **하트비트 타임아웃(1.5초) < 제스처 쿨타임(2초)** — 워치독은 `heartbeat_timeout_s`(`drive_ver2/config.py:34`, **1.5초**) 동안 하트비트가 없으면 자동 정지(`dashboard_control.py:151-157`). 현재는 **브라우저 대시보드가 500ms 간격으로 `/api/control/heartbeat`를 계속 보내는 것**(`dashboard.js:353-365`, `startDriveHeartbeat`)이 이 신호의 유일한 소스이고, START 재호출도 부수적으로 `_last_heartbeat`를 갱신함(`dashboard_control.py:135`). 그런데 제안한 **2초 쿨타임은 1.5초 워치독보다 길다** — 즉 **가속/감속 제스처만으로는 하트비트를 대신할 수 없음**. 브라우저 탭이 열려서 500ms 하트비트를 계속 보내고 있어야만 차량이 순항 구간(제스처 없는 구간)에서도 자동 정지하지 않음. 제스처 인식이 대시보드와 별개의 독립 프로세스(PC에 웹캠 붙여 도는 별도 스크립트)로 돌 계획이라면, **"대시보드 탭이 항상 열려 있어야 한다"는 전제를 명시하거나, 제스처 스크립트가 별도로 `/api/control/heartbeat`를 주기적으로 호출**하게 해야 함.

5. **(해결) GPIO 정지와 HTTP 가속/감속이 서로의 상태를 모르는 문제** — 처음엔 정지가 GPIO 직결, 가속/감속이 HTTP라 두 채널의 상태가 어긋날 위험을 지적했었음. 그런데 §3-1에서 확인했듯 지금은 **점퍼케이블 연결 자체가 불가능**해 정지도 HTTP로 옮겨야 하는 상황이 됐고, 이 참에 **정지·가속·감속을 전부 같은 HTTP 채널(PC `web_dashboard` 경유)로 통일**하기로 함 — 두 채널이 아니라 하나뿐이라 애초에 상태 불일치가 생길 여지가 없어짐. (자세한 내용은 §3-1-1 참고)

6. **설정 중복** — 최대 속도가 PC 쪽(`web_dashboard/config.py:41` `MAX_TARGET_SPEED_MPS`)과 Pi 에이전트 쪽(`drive_ver2/dashboard_control.py` 생성자 `max_speed_mps`, `drive_ver2/config.py`에서 옴) **두 곳에 따로 설정**됨. 기존에도 있던 구조지만, 제스처가 상한 근처까지 반복적으로 밀어붙이는 시나리오가 되면 두 값이 어긋났을 때의 불일치(한쪽은 허용, 한쪽은 거부)가 더 자주 드러날 수 있음.

#### 3-2-4. 요약 — 반영해야 할 설계 결정

- [ ] 제스처 클라이언트는 Pi 에이전트가 아니라 **`web_dashboard`(app.py)를 통해서만** `/api/control/start`, `/api/control/status`, `/api/control/heartbeat` 호출
- [x] 목표 속도 계산은 매 스텝 `round(current ± 0.05, 2)`로 고정 — **구현 완료**. 결과가 하한 이하면 STOP 대신 **하한 클램프(no-op)**로 결정(§4-3, STOP 분기는 재출발 조건과 엉키는 버그가 있어 폐기)
- [ ] 사람의 슬라이더 조작과 제스처 자동 조작이 충돌하지 않도록 우선순위/타임아웃 규칙 결정
- [x] 제스처 컨트롤러가 **자체적으로 `/api/control/heartbeat`를 0.5초 주기로 호출**하도록 구현 완료(`gesture_controller.py`의 하트비트 스레드)
- [x] ~~GPIO 정지(3-1)와 HTTP 상태(3-2)의 불일치 가능성~~ — 점퍼케이블 연결 불가로 정지도 HTTP로 통합 확정(§3-1), 채널이 하나가 되어 해소
- [x] 정지→재출발 시 정지 전 목표 속도를 기억했다가 복원하는 로직 — **표지판(STOP_SIGN) 해제 시에만 구현**. 주먹(FIST)은 자동 재출발 자체를 없앴으므로 해당 없음(§4-2)
- [ ] 제스처 프로세스가 2호기에서 돈다면 `state_api.py`의 기존 `vehicle_state`를 재사용할지, PC `/api/control/status` 폴링으로 갈지 결정 (§3-1-보충)

### 3-3. 그 외 검토 항목 (TODO)

- 오탐지 방지: 손 미검출/`Unknown`/모호한 자세 시 이전 상태 유지 또는 무동작 처리 규칙
- 응답 속도: 실시간 주행 제어에 필요한 프레임 처리 지연 허용치
- 기존 파이프라인과의 관계 — 이 기능은 `features/image_analysis/`(작물 상태 판정용 이미지 분석)와 별개의 흐름(실시간 제어용)인지, 아니면 같은 카메라/파이프라인을 공유하는지
- 기존 YOLO-World/Florence-2 검토 이력([`vision/이미지분석_구현설계.md`](../../../vision/이미지분석_구현설계.md))과는 무관 — 작물 분석이 아닌 주행 제어이므로 별개 기능으로 취급
- 안전 설계: 정지 명령의 우선순위(가속/감속 중에도 정지 인식 시 즉시 정지), 오인식으로 인한 급가속 방지책
- GPU/CPU 리소스 부담 (기존 제약: GTX 1650 Ti 4GB) — 이미지 분석/정지 표지판 인식과 동시 구동 시 부하
- 도입 시 이 파트(VIS)의 담당 범위 변화 여부 — 기존엔 "이미지 저장→전송"까지였으나, 실시간 제어 기능은 범위 확장에 해당

## 4. 구현 현황 (1차 코드 작성 완료, 실기 미검증)

사용자 확인 사항(제스처 인식은 2호기에서, stop_sign과 카메라 공유, 3개 제스처 모두)을 반영해 아래와 같이 구현함.

### 4-1. 폴더 구조

```
features/mediapipe/system/pi2_gesture/
├── gesture_recognizer.py         MediaPipe Gesture Recognizer 래퍼 (VIDEO 러닝 모드)
├── gesture_controller.py         디바운스·쿨타임·정지사유(latch)·속도클램프 상태 머신
├── gesture_config.py             임계값·속도 스텝·1호기/PC URL·토큰 설정
├── test_gesture_controller.py    상태 머신 단위 테스트 (Mock, 하드웨어 불필요) — 11개 통과
└── requirements.txt              mediapipe, opencv-python

features/stop_sign/system/pi2_detector/
├── detector.py                   (수정) 표지판 인식 루프에 제스처 인식 통합, GPIO 제거
├── vehicle_control_client.py     (신규) 1호기 직접 STOP/START (PC 안 거침)
├── dashboard_client.py           (신규) PC web_dashboard 경유 속도조절/상태조회/하트비트
├── config.py                     (수정) GPIO_OUT_PIN 제거
└── requirements.txt              (수정) gpiozero 제거
```

카메라를 공유하기로 해서 별도 프로세스 대신, 기존 `detector.py`의 프레임 루프 안에서 표지판 인식(YOLO)과 제스처 인식(MediaPipe)을 같은 프레임에 대해 함께 실행하도록 합침.

### 4-2. 핵심 설계 — "정지 사유(reason) 집합" 공유 래치

표지판 검출과 주먹 제스처가 같은 1호기를 공유해서 정지시키므로, `GestureController`가 `{"STOP_SIGN", "FIST"}` 같은 사유 집합을 들고 있다가 **어느 하나라도 남아있으면 정지 유지**하도록 만듦 (`request_stop(reason)`). `detector.py`의 기존 표지판 디바운스 루프도 GPIO 대신 이 API를 호출하도록 바꿈 — 표지판이 잡혀있는 동안 주먹을 쥐었다 풀어도 섣불리 재출발하지 않는 것을 단위 테스트로 확인함.

**주먹(FIST)은 자동 재출발 없음 — 정지 전용.** 초기 구현에서는 표지판(STOP_SIGN)과 대칭적으로 "주먹을 풀면 정지 전 속도로 자동 재출발(`send_start`)"하도록 만들었으나, 이는 **요청한 적 없는 기능**이라는 피드백을 받아 제거함. 최종 동작:
- `STOP_SIGN` 사유: 표지판이 사라지면 (원래 stop_sign 기능대로) **자동 재출발** — `release_stop("STOP_SIGN")` 사용
- `FIST` 사유: 주먹을 풀어도 **재출발 명령을 보내지 않음** — 다음 주먹을 다시 인식할 수 있도록 내부 상태만 정리(`_stop_reasons.discard("FIST")`)하고, `release_stop()`(자동 재출발 포함)은 호출하지 않음. 재출발은 사람이 대시보드에서 직접 하는 별개 동작.
- 두 사유가 겹쳐 있을 때(표지판 보이는 동안 주먹도 쥔 경우)는 표지판만 사라져도 주먹이 남아있으면 여전히 재출발 안 함 — 단위 테스트(`test_fist_never_auto_resumes_even_as_sole_reason`)로 확인.

### 4-3. 코드 작성 중 발견해 수정한 설계 오류

원래 §3-1-4에서 "감속으로 속도가 0 이하가 되면 STOP으로 분기"라고 제안했었는데, 실제로 상태 머신을 구현하면서 문제를 발견함: 그 STOP을 주먹(FIST)과 같은 사유로 처리하면, 실제로 주먹을 쥔 게 아니므로 다음 프레임부터 바로 "주먹 미검출" 디바운스가 쌓여 **거의 즉시 자동 재출발**해버려 정지-재출발이 반복되는 깜빡임이 생김. **수정**: 감속은 `MIN_SPEED_MPS(0.05)`를 하한으로 그냥 클램프(무시)하고 정지로 전환하지 않는 것으로 변경 — 실제 정지가 필요하면 주먹 제스처라는 명확한 경로를 쓰도록 함. (`test_gesture_controller.py::test_decelerate_floors_at_min_speed_without_stopping`로 회귀 방지)

### 4-4. PC 웹캠으로 1차 실측 (2호기 실기 아님, 개발 PC에서 검증)

라즈베리파이 실기가 없는 상태에서, 개발 PC(`BisonCam,NB Pro` 내장 웹캠)로 파이프라인 자체가 동작하는지 먼저 확인함.

- **환경**: 별도 venv에 `mediapipe`, `opencv-python` 설치(Windows에서는 문제없이 설치됨), 공식 `gesture_recognizer.task` 모델 다운로드
- **버그 발견 및 수정 1건**: `recognize_for_video()`에 밀리초 단위 경과시간을 그대로 넘겼더니, 루프가 빨리 돌 때 같은 ms 값이 중복돼 `ValueError: Input timestamp must be monotonically increasing`로 **크래시**함. `gesture_recognizer.py`에 타임스탬프를 내부적으로 강제 단조증가시키는 방어 코드를 추가해 수정 — `detector.py`도 동일 패턴이라 실기에서도 터졌을 문제였음
- **오탐지 확인**: confidence 임계값을 테스트 편의상 0.5로 낮췄을 때, 아무 제스처도 안 한 상태에서 `Thumb_Down`이 confidence 0.97까지 잘못 인식됨. 원인은 카메라가 얼굴 쪽을 향해 있어 **턱을 괴는 등 의도치 않은 손 위치**가 화면에 걸렸기 때문으로 추정(스냅샷으로 확인). production 기본값(임계값 0.6)으로 60초간 손 없이 재시험하니 오탐지 0건 — 다만 1회 시험일 뿐이라 "완전히 해결"이라 단정할 근거는 아님. **오탐지는 여전히 실사용 시 주의가 필요한 리스크로 남겨둠** (§3-3의 "오탐지 방지" TODO와 연결)
- **정상 인식 확인**: 실제로 주먹(`Closed_Fist`)과 따봉(`Thumb_Up`)을 보여줬을 때 `gesture_recognizer` → `gesture_controller` → (모킹된) `vehicle_control_client`/`dashboard_client` 전체 파이프라인이 디바운스·쿨타임 포함해 정확히 한 번씩만 STOP/속도변경을 전송함을 확인 (역따봉은 미시험)
- **결론**: 인식 파이프라인 자체는 PC에서 정상 동작 확인됨. 다만 이건 라즈베리파이 실기, 실제 1호기, 실제 사이드카메라 각도/거리와는 다른 조건이라 §4-5의 TODO는 여전히 유효함

### 4-5. 2호기 실기 테스트 — 치명적 블로커 발견 (mediapipe가 이 Pi에서 실행 자체가 안 됨)

`192.168.2.28`(hostname `VIS`, Raspberry Pi 4 Model B Rev 1.5, aarch64, Python 3.13.5)에 SSH로 직접 접속해 확인함. 카메라는 `/dev/video0`의 **Logitech HD Pro Webcam C920**(기존 stop_sign이 "작물 카메라 임시 대여" 명목으로 이미 쓰고 있던 장치와 동일).

**설치는 됨**: `pip install mediapipe`가 PEP 668(`--break-system-packages` 필요) 외엔 문제없이 성공 — aarch64+Python 3.13용 wheel(`mediapipe-1.0.1`, 그리고 `1.0.0`)이 실제로 존재함. §7-1에서 우려했던 "Pi에 설치가 안 될 수도 있다"는 걱정은 **기우였음**.

**하지만 실행하면 즉시 크래시함**:
```
FATAL ERROR: This binary was compiled with aes enabled, but this feature is not available on this processor (go/sigill-fail-fast).
```
- **원인 확인**: `/proc/cpuinfo`의 `Features`에 `aes`가 없음 — 이 Pi의 Cortex-A72(BCM2711)는 ARM 암호화 확장(AES 하드웨어 명령어)을 지원하지 않는 CPU. 그런데 PyPI의 mediapipe aarch64 wheel은 이 명령어가 있다고 가정하고 빌드돼 있어서, 실행 시점에 곧바로 SIGILL로 죽음
- **버전 문제 아님**: 이 Python 3.13 환경에 설치 가능한 mediapipe는 `1.0.0`/`1.0.1` 둘뿐인데(더 오래된 0.10.x대는 Python 3.13 wheel 자체가 없음), **둘 다 동일하게 크래시**함
- **환경변수 우회 시도**: `GODEBUG=cpu.aes=off`, `XNNPACK_FORCE_NO_AES=1`, `MEDIAPIPE_DISABLE_GPU=1` 등을 시도했으나 전부 무효 — mediapipe는 Go가 아니라 C++(Bazel) 빌드라 Go 런타임의 크립토 우회 옵션이 애초에 적용 대상이 아님. 웹 검색으로도 이 특정 조합(mediapipe + ARM 크립토 확장 미지원 CPU)에 대한 알려진 우회법을 찾지 못함
- **PC에서는 왜 됐는가**: §4-4의 PC 테스트는 x86_64(Windows)라 이 문제 자체가 발생하지 않음 — "PC에서 파이프라인이 동작함을 확인"한 것과 "이 Pi에서 동작함"은 완전히 별개의 결과였음

### 4-6. 후속 조사 — MediaPipe의 편의 API만 막힌 것, 밑단(TFLite)은 정상 동작함

"진짜 이 CPU에선 아예 안 되는 건지" 더 파봄.

1. **`mediapipe-rpi4`(piwheels, Pi4 네이티브 빌드) 시도 → 막다른 길**: `pip install mediapipe-rpi4`는 성공했으나(버전 0.8.8, 2021년산), import하면 `ModuleNotFoundError: No module named 'mediapipe.python._framework_bindings'` — **네이티브 컴파일 바인딩이 통째로 빠진 불완전한 패키지**였음(오래 방치된 빌드로 추정). 이 경로는 폐기.
2. **크래시 발생 지점을 좁힘**: `import mediapipe`는 되고, `GestureRecognizerOptions` 객체 생성도 되는데, **`GestureRecognizer.create_from_options()`(그래프 러너 초기화) 호출 시점에만** SIGILL 발생. 즉 계산(추론) 자체가 아니라 MediaPipe Tasks의 그래프 초기화 코드 어딘가의 문제.
3. **결정적 실험 — bare TFLite는 이 CPU에서 멀쩡함**: `gesture_recognizer.task`는 사실 zip 아카이브라서, 안에 든 실제 서브 모델들을 꺼낼 수 있음(`hand_detector.tflite`, `hand_landmarks_detector.tflite`, `gesture_embedder.tflite`, `canned_gesture_classifier.tflite`). 이 중 `canned_gesture_classifier.tflite`를 MediaPipe 없이 **순수 TFLite 런타임(`ai-edge-litert`, XNNPACK 델리게이트 포함)으로 직접 로드**했더니 인터프리터 생성·텐서 할당까지 **정상 동작**함 (`INFO: Created TensorFlow Lite XNNPACK delegate for CPU.` 로그까지 정상 출력, 크래시 없음).
4. **레거시 `mp.solutions` API 확인**: mediapipe 1.0.x는 구버전 Solutions API(`mp.solutions.hands` 등)를 아예 제거해서 이 우회로도 없음(`hasattr(mp, 'solutions')` → `False`).

**정정된 결론**: "이 하드웨어에서 손동작 인식이 원천적으로 불가능"이 아니라, **"MediaPipe Tasks의 편의 API(그래프 러너)만 이 CPU에서 막혀 있고, 그 아래 실제 추론 엔진(TFLite+XNNPACK)은 완전히 정상 동작한다"**는 것으로 밝혀짐. `.task` 파일 안에 이미 손 검출→랜드마크→임베딩→분류 4단계 모델이 다 들어있으므로, **MediaPipe의 그래프 러너를 안 쓰고 이 4개 모델을 직접 순서대로 호출하는 파이프라인을 자체 구현**하면 우회 가능 — 다만 MediaPipe의 손 추적 파이프라인 일부(검출 박스 NMS, 손 영역 크롭·회전 정규화, 랜드마크→임베딩 변환 등)를 직접 재구현해야 해서 코드량이 상당함.

중간에 2호기가 네트워크에서 잠깐 응답 없음 상태(ping 100% 손실)가 됐다가 자연 복구됨 — 이 세션에서 뭔가를 잘못 건드려서 끊긴 흔적은 없고, 이전에도 같은 증상이 있었음. 재접속 후 이어서 진행함.

### 4-7. 자체 파이프라인 v1 — 실제로 동작함 (정확도는 아직 미흡)

4개 서브 모델의 입출력 텐서를 이름 기준으로 확인 후, **팜(손) 디텍터 단계는 생략하고 프레임 전체를 224×224로 리사이즈해 랜드마크 모델에 바로 넣는 단순화 버전**을 짜서 2호기의 실제 C920 웹캠으로 10초간 돌림 (`extracted/raw_pipeline_test.py`).

- 분류기 출력 8개 클래스 순서를 모델에 내장된 `labels.txt`에서 직접 확인함: `None, Closed_Fist, Open_Palm, Pointing_Up, Thumb_Down, Thumb_Up, Victory, ILoveYou`
- landmark → embedder → classifier 3단계는 텐서를 그대로 이어 붙이기만 하면 됨(모델들이 원래 그래프에서 서로 그렇게 연결되도록 설계돼 있어서 별도 좌표 변환 불필요) — 이 부분은 별문제 없이 맞아떨어짐
- **결과: 크래시 없이 끝까지 동작함.** 손을 화면 중앙에 크게 대고 있던 구간(1586~3687ms)에서는 `Open_Palm`이 33프레임 연속 안정적으로 잡힘(presence 0.73, score 0.6~0.75대) — 실제 인식이 되는 것을 확인
- **문제**: 팜 디텍터(정상 MediaPipe라면 손 위치를 찾아 크롭·회전 정규화하는 단계)를 생략해서, 손이 화면에 작게/치우쳐 잡히는 구간에서는 `presence`가 0.5 근처(모델이 거의 확신 못 하는 경계값)를 맴돌며 `None`/`Closed_Fist`/`Pointing_Up`/`Thumb_Down` 사이를 프레임마다 튀는 노이즈가 심함
- **의미**: "이 하드웨어에서 MediaPipe 급 손동작 인식이 불가능하다"는 이전 결론은 **틀렸음** — AES 크래시는 완전히 우회됐고, 실제 인식도 동작함. 다만 지금 버전(v1)은 팜 디텍터가 빠져 있어 프레이밍에 매우 민감하고 정확도가 낮음. 정식 채택하려면 팜 디텍터(anchor 디코딩 + NMS + 크롭/회전)를 마저 구현하거나, 최소한 손이 화면 중앙에 크게 나오도록 사용 조건을 제한해야 함

### 4-8. 자체 파이프라인 v2 — 팜 디텍터 구현 시도, 부분 성공 (크롭/회전에 남은 버그)

v1의 "팜 디텍터 생략" 문제를 고치려고 `hand_detector.tflite`(2016 anchor 출력)를 실제로 디코딩하는 v2를 구현함(`extracted/raw_pipeline_v2.py`).

- **anchor 생성**: 추측이 아니라 MediaPipe 공식 `ssd_anchors_calculator.cc` 소스를 직접 확인해서 그대로 포팅함(`num_layers=4, strides=[8,16,16,16], aspect_ratios=[1.0], interpolated_scale_aspect_ratio=1.0` → 위치당 앵커 2개). 실행 결과 정확히 **2016개** 생성돼 모델 출력 shape과 일치함을 확인 — 이 부분은 신뢰도 높음
- **디코딩 + NMS**: box regression(18값: bbox 4 + keypoint 7쌍) + sigmoid(score) + greedy NMS 구현
- **결과 1 — 디텍터 자체는 확실히 동작함**: 실제 웹캠에서 `det_score` 0.85~0.98의 높은 신뢰도로 안정적으로 손을 찾아냄(9초 넘게 연속 추적한 구간도 있었음), 손을 치우면 "검출 안 됨"도 정확히 나옴 — v1때 존재조차 안 하던 신호가 이제 확실히 살아있음
- **결과 2 — 하지만 손 크롭(회전+정규화) 단계에 아직 버그가 남아있음**: 디버그로 저장한 크롭 이미지를 직접 열어보니 **손목/팔뚝만 잡히고 손가락·손바닥은 프레임 밖으로 잘려나감**. 회전각도 프레임마다 -45°~-92°로 안정적인 손 자세치고는 과하게 들쭉날쭉함. 그 결과 랜드마크 모델의 `presence`가 디텍터가 9초 이상 안정적으로 손을 잡고 있던 구간에서도 계속 0에 가까운 값(0.00~0.02)에 머묾 — 분류 결과도 여전히 실제 제스처와 무관하게 `None`/`Open_Palm` 위주로 나옴
- **원인 후보(아직 미확정)**: (a) 크롭 중심을 손 전체가 아니라 감지 박스 중심으로만 잡아서, 카메라 각도상 손가락이 위쪽 프레임 밖으로 나가는 경우를 못 잡음 (b) 손목(kp0)→중지뿌리(kp2) 방향으로 회전각을 계산하는 로직의 좌표계/부호 규약이 실제 MediaPipe 컨벤션과 다를 가능성 (c) 크롭 스케일(현재 손 박스의 2배)이 여전히 안 맞을 가능성. SSH로 로그만 보며 반복 조정하는 방식은 한계가 있어서, 고정된 테스트 이미지 + 랜드마크 시각화(박스·회전축을 이미지에 그려서 확인) 방식으로 차분히 디버깅해야 더 빠르게 잡힐 것으로 보임

### 4-9. 다음 단계

1. **[진행 중, 최우선] 크롭/회전 버그 수정** — 고정 테스트 이미지에 디텍터 박스·keypoint·회전축을 그려서 시각적으로 검증하는 방식으로 전환 필요. MediaPipe 공식 `hand_landmark_cpu.pbtxt`의 `shift_y`/`scale_x`/`scale_y` 정확한 값도 재확인 필요(지금은 대략값 사용 중)
2. 크롭이 맞으면 v1에서 이미 확인한 것처럼 landmark→embedder→classifier 배선 자체는 문제없이 맞물릴 것으로 예상(§4-7에서 검증됨)
3. mediapipe를 소스에서 이 CPU(크립토 확장 없음) 타겟으로 재빌드 — 위 자체 파이프라인이 완성되면 불필요해짐
4. 다른 하드웨어에서 인식을 돌리는 구조 — 마찬가지로 위가 완성되면 불필요

- [ ] `gesture_config.py`의 `DRIVE_PI_CONTROL_URL`/`DRIVE_PI_CONTROL_TOKEN`/`DASHBOARD_URL`을 실제 값으로 교체
- [ ] 2호기 실기에서 표지판 인식 + 제스처 인식(자체 파이프라인) 동시 구동 시 CPU 부하 실측 — 크롭 버그 해결 후
- [ ] 디바운스 N/M, confidence 임계값, 쿨타임 2초 값 실측 후 조정 — 특히 오탐지(§4-4) 재현 여부를 실제 사이드카메라 각도로 다시 확인 필요
- [ ] 실제 1호기·PC 장비와 end-to-end 통합 테스트
- [ ] 3개 제스처(주먹/따봉/역따봉) 모두 자체 파이프라인으로 정확히 분류되는지 재확인 (크롭 버그 수정 후)

## 5. 결론

**진행 중 — 크래시 문제는 완전히 해결됐고 파이프라인 골격도 세워졌지만, 아직 실사용 가능한 정확도는 아님.**

- ✅ 공식 mediapipe는 이 Pi(Cortex-A72, AES 미지원)에서 크래시하고 우회법도 없음(§4-5) — **확정**
- ✅ 그 아래 TFLite+XNNPACK 엔진은 이 CPU에서 정상 동작함 — **확정**
- ✅ 손 검출(팜 디텍터) 단계를 anchor 디코딩+NMS까지 구현해서 실제로 높은 신뢰도(0.85~0.98)로 안정적으로 손을 찾아냄 — **확정**
- ❌ 검출된 손 영역을 랜드마크 모델용으로 크롭·회전하는 단계에 아직 버그가 있어, 최종 제스처 분류는 아직 신뢰할 수 없음 — **미해결, 디바운스·쿨타임 등 상위 로직(gesture_controller.py)은 이미 검증됐으니 이 마지막 단계만 고치면 전체가 이어질 것으로 예상**

**요약**: "이 하드웨어에선 안 된다"는 확실히 틀렸고, 골격(디텍터+엔진)은 다 동작함을 실측으로 증명했다. 남은 건 크롭/회전 정규화라는 특정 버그 하나 — 채택 여부를 결론짓기 전에 이것부터 마저 고쳐야 함.
