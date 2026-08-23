# VIS 기능 3 — AI 이미지 분석 구현 설계

> 작성일: 2026-08-22
> 대상 기능: 비전 인식(VIS) 파트 중 "저장·전송된 작물 이미지를 분석해 상태(정상/위험/심각) 판정" 기능
> 위치: `features/image_analysis/design/`
> 참고: 원래 검토했던 모델 비교·판정 로직은
> [`vision/이미지분석_구현설계.md`](../../../vision/이미지분석_구현설계.md),
> [`vision/model/image_recognition_and_analysis.md`](../../../vision/model/image_recognition_and_analysis.md) 참고.

---

## 1. 원래 계획했던 분석 방법

`①이미지 저장 → ②전송`까지 구현한 뒤, `③AI 분석` 단계는 아래와 같이 **자체 비전 모델을 돌리는 방식**으로 계획했었습니다.

### 1-1. 모델 후보 비교 — YOLO-World vs Florence-2

라벨링된 데이터셋이 없고 일정이 촉박한 상황이라, 학습 없이 텍스트 프롬프트만으로 바로 쓸 수 있는 **제로샷 탐지 모델** 두 가지를 검토했습니다.

| 항목 | YOLO-World | Florence-2 |
|---|---|---|
| 성격 | 객체 탐지 전용 모델 | 범용 비전-언어 모델 |
| 출력 형태 | 박스+클래스+confidence (숫자) | 자연어 캡션 (텍스트) |
| 상태 판정 방식 | confidence·면적 등 숫자 기준으로 규칙화 가능 | 캡션에서 상태 키워드 파싱 필요(표현 편차 위험) |
| 정확도(NFR_204 80%) 검증 | Precision/Recall 직접 측정 가능 | 캡션 기반이라 정량 측정 어려움 |
| 라이선스 | AGPL-3.0 | MIT |
| GPU 부담(GTX 1650 Ti 4GB 기준) | 상대적으로 빠듯 | 여유로움 |

**1차 채택안**: 숫자 기반 출력이 판정 로직과 정확도 검증(NFR_204) 둘 다에 유리하다는 이유로 **YOLO-World**를 1차로 제안. Florence-2는 리포트용 자연어 설명이 필요해질 경우 보조 모델로 병행하는 방향을 검토했습니다.

### 1-2. 상태 판정 규칙(초안)

YOLO-World는 박스+클래스+confidence만 주므로, 아래처럼 규칙 기반 판정 로직을 직접 작성하는 방식을 계획했습니다.

| 상태 | 판정 규칙(초안) |
|---|---|
| 정상 | 질병 관련 클래스 미탐지, 또는 confidence < 0.4 |
| 위험 | 질병 클래스 탐지, confidence 0.4~0.7 또는 병반 면적 비율 5~20% |
| 심각 | confidence ≥ 0.7 또는 병반 면적 비율 20% 초과 또는 한 이미지에서 질병 탐지 2건 이상 |

(수치는 실측 전 초안이며, 실제 데이터로 임계값을 다시 잡아야 한다고 명시했었음)

### 1-3. 파이프라인 및 인터페이스

```
[라즈베리파이] --(①②)--> [PC server.py] --(③)--> analyze.py --> analysis_results/*.json --> AI 리포트 파트
```

- `analyze.py`가 `received/날짜/` 폴더에 도착한 이미지를 배치 처리
- 결과는 DB/API가 아니라 **JSON 파일**(`analysis_results/날짜/batch_*.json`)로 AI 리포트 파트에 전달 — 팀 컨벤션(파일 기반 상태 관리) 일치, 결합도 최소화가 이유
- JSON에 `detections`(class/confidence/bbox/area_ratio), `status`, `status_reason`, `summary`(상태별 집계)를 포함하는 스키마안까지 마련

---

## 2. 현재 구현 방식 — 자체 모델 대신 다른 담당자의 LLM API 사용

위 1번의 자체 비전 모델(YOLO-World/Florence-2) 방식은 **채택하지 않았고**, 현재는 **AI 분석을 담당하는 다른 팀원이 구축한 LLM API를 호출해 분석 결과를 받는 방식**으로 구현되어 있습니다.

- 이미지 인식·상태 판정 모델을 이 파트에서 직접 돌리지 않고, 완성된 이미지를 다른 담당자의 LLM API에 전달 → 응답으로 분석 결과를 받는 구조
- 위 1번 문서(모델 비교, 판정 규칙, 자체 JSON 스키마안)는 **검토 이력으로 보존**하되, 실제 파이프라인 구현의 기준은 아님
- 이미지 저장(①)·전송(②)까지는 기존 `features/image_transfer/` 구조를 그대로 사용하고, 전송된 이미지를 LLM API에 넘겨 분석 결과를 받는 부분만 이 폴더(`features/image_analysis/`)에서 다룸

➡ **참고**: LLM API 연동의 구체적인 요청/응답 형식, 인증 방식 등은 해당 API를 담당하는 팀원 쪽 문서/코드를 확인 필요.

---

## 3. 결론 — 이 파트(VIS) 담당 범위 아님

`③AI 분석` 단계는 위와 같이 **다른 담당자의 LLM API를 그대로 호출**해서 처리하는 구조로 확정되었으므로, 모델 선정·판정 로직·정확도 검증(NFR_204 등)은 **VIS(비전 인식) 파트가 아니라 해당 LLM API 담당자의 책임 범위**입니다.

- VIS 파트가 실제로 구현·유지보수하는 범위는 `①이미지 저장 → ②전송`(`features/image_transfer/`)까지이며, 여기서 만든 이미지를 LLM API로 넘기는 연동 지점까지만 관여
- 1번에 정리된 YOLO-World/Florence-2 모델 비교, 판정 규칙, JSON 스키마안은 **채택되지 않은 검토 이력**일 뿐, VIS 파트가 앞으로 구현·검증해야 할 항목이 아님
- 따라서 분석 정확도·모델 교체·판정 임계값 튜닝 등에 대한 후속 논의는 VIS 파트가 아니라 **LLM API 담당자**와 진행되어야 함

## 4. 2026-08-23 업데이트 — 실제로 구현됨

위 2번에서 말한 "다른 담당자의 LLM API 호출" 단계를 실제로 구현한 스크립트가
`vision/image_analysis/system/classify.py`에 추가되었다. `ai_report/` 코드는
전혀 건드리지 않고, `01-interface-contracts.md` §C2.1/§C2.2가 정의하는
파일 계약(`data/images/{patrol_id}/`, `data/analysis/{patrol_id}/*.json`,
`_COMPLETE` 마커)만 채워 넣는다.

```bash
python -m vision.image_analysis.system.classify \
    --patrol-id 20260824_0900 \
    --source-dir vision/image_transfer/system/pc_server/received/2026-08-20
```

`--patrol-id`는 자동 판별하지 않는다 — 촬영된 파일명(`received/{date}/{ts}_cam01_{seq}.jpg`)만으로는
어떤 순찰(patrol)에 속하는지 알 수 없고, 잘못 추정하면 구역 판정
(`pipeline/segment.py`, hard rule 4)이 조용히 틀어지기 때문에 사람이 직접
지정하도록 했다 — `devtools/fake_vis.py --patrol-id`와 동일한 방식이다.

실제 API로 검증됨: `vision/image_transfer/system/pc_server/received/`의
실제 사진 2장(천장 조명 사진)으로 실행해, 정상적으로 "작물 없음" (`detections: []`)
결과를 반환하고 `data/analysis/{patrol_id}/{image_id}.json` + `_COMPLETE`가
올바르게 생성되는 것을 확인했다. 테스트는 `tests/test_vision_classify.py` —
특히 `ai_report.ingest.vis_watcher.VisWatcher.scan_once`(운영 코드에서
`orchestration.py::run_patrol_pipeline`이 실제로 호출하는 바로 그 함수)로
직접 재소비해보는 라운드트립 테스트가 계약 준수를 가장 강하게 증명한다.
