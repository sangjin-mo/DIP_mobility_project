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
| 대시보드 PC의 WEB 서버 | 브라우저 단일 진입점 | `http://PC_IP:8080` |

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

구동 담당자가 현재 `drive/manage.py`와 `drive/dashboard_control.py`를 실행
가능한 상태로 준비한다. 현재 구성은 학습 모델을 이용한 조향을 요구하므로 실제
모델 경로를 지정해 DonkeyCar 루프를 시작해야 한다.

```bash
cd drive
python manage.py drive --model=models/mypilot.h5
```

정상 시작 시 `:9200/api/control`이 열린다. 토큰, 최대 스로틀, 모델 경로,
조향 보정은 구동 담당 영역이다. WEB은 이 파일들을 수정하거나 PWM/GPIO를 직접
제어하지 않는다.

## 3. 웹캠 Raspberry Pi

비전 담당자가 `vision/image_transfer/system/pi_agent/config.py`의 PC 전송
주소가 `PC_IP:8000`을 가리키는지 확인한다. 이 설정은 비전 담당 소유이므로
WEB 코드에서 덮어쓰지 않는다.

터미널 1에서 정지 이미지를 계속 저장한다.

```bash
cd vision/image_transfer/system/pi_agent
python capture.py
```

터미널 2에서 PC의 전송 요청을 기다린다.

```bash
cd vision/image_transfer/system/pi_agent
uvicorn upload_server:app --host 0.0.0.0 --port 8001
```

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

## 5. PC의 WEB 대시보드

저장소 루트의 `.env`에 아래 값을 넣는다. 실제 비밀 문자열은 Git에 커밋하지
않는다.

```dotenv
DASHBOARD_VISION_SERVER_URL=http://127.0.0.1:8000
DASHBOARD_ROVER_CONTROL_URL=http://DONKEY_PI_IP:9200/api/control
DASHBOARD_ROVER_CONTROL_TOKEN=CONTROL_SECRET
DASHBOARD_DEFAULT_TARGET_SPEED_MPS=0.25
```

실행한다.

```powershell
cd C:\path\to\DIP_mobility_project
.\.venv\Scripts\python.exe -m web_dashboard
```

`http://127.0.0.1:8080`을 연다. 다른 기기에서는 Windows 방화벽에서 8080번
포트를 허용한 뒤 `http://PC_IP:8080`으로 접속한다.

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
