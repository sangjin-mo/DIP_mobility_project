# Raspberry Pi 2대 연동 실행 안내

이 문서는 다른 팀의 `drive/`, `vision/`, `ai_report/` 소스를 수정하지 않고
WEB 대시보드가 각 팀이 공개한 입출력만 사용하는 구성을 설명한다.

## 구성

| 장치 | 역할 | 대시보드와 연결되는 API |
| --- | --- | --- |
| DonkeyCar Raspberry Pi | 차선 유지 카메라, 조향·모터 | `POST :9200/api/control` |
| 웹캠 Raspberry Pi | 1초 간격 정지 이미지 저장, 요청 시 PC 전송 | `POST :8001/trigger-upload` |
| 대시보드 PC의 VIS 서버 | 웹캠 Pi 전송 요청, 이미지 수신·목록 제공 | `POST :8000/control/request-transfer`, `GET :8000/images` |
| 대시보드 PC의 AI/LLM | `reports/<patrol_id>/` 생성 | 파일 기반 `metadata.json`, `report.md` |
| 대시보드 PC의 WEB 서버 | 브라우저 단일 진입점(제어탑) | `http://<PC 로컬 호스트 이름>.local:8080` |

카메라 촬영과 차량 제어는 서로 독립적이다. 카메라 버튼은 차량의 START,
STOP API를 호출하지 않는다.

## 1. 먼저 정해야 할 값

- `DONKEY_PI_IP`: DonkeyCar Raspberry Pi 주소
- `WEBCAM_PI_IP`: 웹캠 Raspberry Pi 주소
- `PC_IP`: VIS 서버와 대시보드를 실행하는 노트북 주소
- `CONTROL_SECRET`: DonkeyCar와 대시보드에 동일하게 설정할 비밀 문자열

세 장치는 같은 네트워크에서 서로의 IP와 아래 포트에 접근할 수 있어야 한다.

- `9200/TCP`: 차량 제어
- `8001/TCP`: 웹캠 이미지 전송 트리거
- `8000/TCP`: PC 이미지 수신 서버
- `8080/TCP`: 대시보드

## 2. DonkeyCar Raspberry Pi

구동 담당자가 실제 Pi의 `~/mycar`에 `dashboard_control.py`와 이를 연결한
`manage.py`가 배포됐는지 확인한다. 실제 Pi 저장소에는 별도의 PWM 보정값이
있으므로 WEB 담당자가 이 저장소의 `drive/config.py`를 통째로 덮어쓰면 안 된다.
현재 구성은 학습 모델을 이용한 조향을 요구하므로 실제 모델 경로를 지정한다.

```bash
cd ~/mycar
export DASHBOARD_CONTROL_TOKEN='충분히-긴-공유-비밀값'
python manage.py drive --model=models/mypilot.h5
```

정상 시작 시 `:9200/api/control`이 열린다. 토큰, 최대 스로틀, 모델 경로,
조향 보정은 구동 담당 영역이다. WEB은 이 파일들을 수정하거나 PWM/GPIO를 직접
제어하지 않는다.

Pi에서 IP와 상태 API를 확인한다.

```bash
hostname -I
curl http://127.0.0.1:9200/api/status
```

## 3. 웹캠 Raspberry Pi

비전 담당자가 `vision/image_transfer/system/pi_agent/config.py`의 PC 전송
주소가 `PC_IP:8000`을 가리키는지 확인한다. 이 설정은 비전 담당 소유이므로
WEB 코드에서 덮어쓰지 않는다.

항상 촬영하는 기존 방식이 필요할 때만 터미널 1에서 `capture.py`를 실행한다.
대시보드의 촬영 모드 버튼을 사용할 때는 같은 카메라를 두 프로세스가 동시에
열지 않도록 이 명령을 실행하지 않는다.

```bash
cd vision/image_transfer/system/pi_agent
python capture.py
```

터미널 2에서 PC의 전송 요청을 기다린다.

```bash
cd vision/image_transfer/system/pi_agent
uvicorn upload_server:app --host 0.0.0.0 --port 8001
```

터미널 3에서는 WEB 팀이 추가한 독립 상태 수신기를 실행한다. 비전팀의
`capture.py`와 `upload_server.py`는 수정하지 않는다.

```bash
cd ~/DIP_mobility_project
export VISION_DRIVE_STATE_TOKEN='PC와 동일한 공유 토큰'
export VISION_CAPTURE_DIR="$HOME/DIP_mobility_project/vision/image_transfer/system/pi_agent/images"
python -m web_dashboard.vision_pi_state_receiver
```

수신기는 기본적으로 `:8002/api/drive-state`를 사용하고 최신 상태를
`vision_drive_state.json`에 저장한다. 최초 관측값은 WEB의 비교 기준으로만
사용하며, 이후 `RUNNING ↔ STOPPED` 등 실제 상태 전환만 전달된다. 0.5초
heartbeat는 비전 Pi로 보내지 않는다.

대시보드에서 `촬영 모드 시작`을 누르면 수신기가 웹캠을 열고 기본 1초 간격으로
위 이미지 경로의 날짜 폴더에 JPEG를 저장한다. `촬영 모드 정지`를 누르면 카메라를
반납한다. 저장된 파일은 기존 `upload_server.py` 전송 대상이므로 `라즈베리파이
사진 불러오기` 버튼으로 PC VIS 서버에 가져올 수 있다.

현재 비전 계약은 버튼을 누르는 순간 카메라를 새로 여는 방식이 아니다.
`capture.py`가 1초마다 저장한 이미지 중 아직 보내지 않은 파일을 전송한다.
따라서 대시보드에는 요청 시점에 가장 가까운 최신 정지 이미지가 표시된다.

## 4. PC의 VIS 이미지 서버

비전 담당자가 `vision/image_transfer/system/pc_server/config.py`의 Pi 주소가
`WEBCAM_PI_IP:8001`을 가리키는지 확인한 뒤 실행한다.

```powershell
cd vision\image_transfer\system\pc_server
python -m pip install -r requirements.txt
python main.py
```

브라우저에서 `http://127.0.0.1:8000/images`를 열어 `images` 배열이 반환되면
VIS 서버가 준비된 것이다.

대시보드의 비전 이미지 관리 화면은 VIS 서버의 기존 계약을 그대로 사용한다.

- `POST /control/request-transfer`: Pi의 미전송 촬영본 가져오기
- `GET /images`: PC에 수신된 이미지 목록
- `POST /images/delete`: 선택한 PC 수신본 삭제
- `POST /control/delete-all-local`: 업로드 완료된 Pi 촬영본 정리

현재 비전 서버에는 단발 촬영 HTTP API와 LLM 분석팀 전송 API가 없다. 그래서
두 버튼은 화면에 연동 대기로 표시되며 성공한 것처럼 처리하지 않는다. 해당
기능을 활성화하려면 담당자로부터 URL, 인증 방식, 요청 본문, 응답 형식을 먼저
받아 WEB 어댑터에 추가해야 한다.

## 5. PC의 WEB 대시보드

저장소 루트의 `.env`에 아래 값을 넣는다. 실제 비밀 문자열은 Git에 커밋하지
않는다.

```dotenv
DASHBOARD_HOST=0.0.0.0
DASHBOARD_VISION_SERVER_URL=http://127.0.0.1:8000
DASHBOARD_VISION_PI_STATE_URL=http://WEBCAM_PI_IP:8002/api/drive-state
DASHBOARD_VISION_PI_STATE_TOKEN=PC와_동일한_공유_토큰
DASHBOARD_ROVER_CONTROL_URL=http://DONKEY_PI_IP:9200/api/control
DASHBOARD_ROVER_CONTROL_TOKEN=CONTROL_SECRET
DASHBOARD_DEFAULT_TARGET_SPEED_MPS=0.25
DASHBOARD_MAX_TARGET_SPEED_MPS=0.50
# Optional: best-effort SSH remote start of the drive Pi's control process
DRIVE_PI_SSH_HOST=DONKEY_PI_IP
DRIVE_PI_SSH_USER=pi
```

`DASHBOARD_HOST`는 반드시 `0.0.0.0`이어야 한다. `127.0.0.1`이면 이 PC에서만
접속되고 다른 노트북에서는 대시보드에 전혀 연결할 수 없다.

`DASHBOARD_MAX_TARGET_SPEED_MPS`는 Pi의 `DASHBOARD_MAX_SPEED_MPS`보다 크게
설정하지 않는다. 슬라이더 값은 START 명령의 `target_speed_mps`로 전달되며,
운행 중 슬라이더를 변경하면 갱신된 START 명령을 다시 전송한다.

대시보드를 실행하기 전에 PC에서 Pi 연결을 확인한다.

```powershell
Test-NetConnection DONKEY_PI_IP -Port 9200
Invoke-RestMethod http://DONKEY_PI_IP:9200/api/status
```

**한 대의 PC만 대시보드 담당 PC가 된다.** 그 PC에서 `ai_report` 수집
리스너, 비전 `pc_server`, `web_dashboard`를 함께 실행한다. macOS에서는
아래 스크립트 하나로 세 가지를 모두 실행하고, 종료 시(Ctrl+C) 방화벽도
자동으로 다시 잠근다.

```bash
cd /path/to/FarmRover
./scripts/start_central_server.sh
```

다른 팀원은 각자 `python -m web_dashboard`를 실행하지 않는다 — 각자의
로컬 `sessions.db`는 비어 있으므로 겉보기엔 정상 작동해도 실제 순찰
데이터를 전혀 보여주지 못한다. 대시보드 헤더의 "대시보드 인스턴스"
배지가 대시보드 담당 PC의 호스트 이름과 공유 데이터 연결 여부를 보여주므로,
잘못된(로컬) 인스턴스에 접속했는지 바로 확인할 수 있다.

다른 기기에서는 대시보드 담당 PC의 macOS 로컬 호스트 이름으로 접속한다
(`scutil --get LocalHostName`으로 확인). 세션마다 바뀌는 DHCP IP 대신
안정적인 주소다.

```text
http://<대시보드 PC 로컬 호스트 이름>.local:8080
```

`scripts/start_central_server.sh`가 실행 중에는 macOS 방화벽을 열어 두므로
Windows 방화벽 수동 설정과 달리 별도 조치가 필요 없다. 스크립트를
종료하면(Ctrl+C) 방화벽도 다시 닫힌다.

## 6. LLM 레포트

AI/LLM 담당 파이프라인이 대시보드와 동일한 `REPORT_ROOT` 아래에 다음 파일을
생성하면 WEB이 최신 순찰 레포트를 자동으로 읽는다.

```text
reports/
  YYYYMMDD_HHMM/
    metadata.json
    report.md
```

대시보드는 LLM을 다시 호출하거나 농작물 판정을 만들지 않는다. 구역 카드에는
검증된 `metadata.json`의 구역·상태·관측 수를, 종합 레포트에는 `report.md`
본문을 표시한다.

## 7. 안전한 시험 순서

1. 차량을 연결하지 않고 WEB 테스트와 fake control agent로 버튼 경로를 확인한다.
2. VIS 서버의 `/images`에 이미지가 보이는지 확인한다.
3. 대시보드의 촬영 버튼으로 최신 이미지가 바뀌는지 확인한다.
4. DonkeyCar 바퀴를 공중에 띄우고 STOP부터 확인한다.
5. START 후 브라우저를 닫거나 Wi-Fi를 끊어 watchdog 정지를 확인한다.
6. 마지막에만 저속 지상 시험을 한다.

이 코드의 자동 테스트는 외부 장치를 모의 응답으로 검증한다. 실제 모터·웹캠
정상 작동 여부는 각 장치가 연결된 상태에서 위 순서로 별도 확인해야 한다.
