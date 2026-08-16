"""The system prompt — spec §9.2, stored verbatim.

Bump `config.PROMPT_VERSION` on any edit to `SYSTEM_PROMPT`'s text; it is
recorded in every report's `metadata.json` (`llm.prompt_version`) and in
every `payload.json` (`Payload.prompt_version`) so results stay comparable
across prompt revisions. A6 adds a test that catches an edit here without a
matching version bump (build plan A6: "Editing the prompt without bumping
PROMPT_VERSION fails a test") — not enforced yet in A5.

The 금지 규칙 (prohibition) block is the part that determines report
quality — see spec §9's note: it prevents two failure modes, inventing
causal claims from six zone-level data points, and asserting plant counts
the pipeline never measured. `llm/client.py::_scan_prohibited_language`
is a defensive runtime backstop for the same two rules — the prompt asking
nicely is not assumed to be 100% reliable.

Called by: `llm/client.py::generate_report`, as the API request's system message.
"""

from __future__ import annotations

SYSTEM_PROMPT = """당신은 농업 생육 진단 보조 시스템이다.
스마트 순찰 로버가 1회 순찰에서 수집한 데이터를 해석하여 진단 리포트의
서술 부분을 작성한다.

[입력 규약]
- 모든 수치(관측 수, 평균, 비율)는 이미 확정되어 제공된다.
- 제공된 수치를 그대로 인용하라. 다시 계산하거나 추정하지 마라.
- 이미지는 수치의 근거 확인과 시각적 소견 기술에만 사용한다.
- 이미지에서 수치를 직접 세지 마라.

[평가 기준]
1. 작물 생육 상태
   정상 / 미성숙 / 병충해_의심 분포와 이미지의 시각적 소견을 함께 기술한다.
2. 환경 조건
   구역별 온습도를 해당 구역에서 함께 관찰된 조건으로 기술한다.
3. 통로 장애 요인
   비상정지 및 라인 이탈 이벤트의 발생 구역과 빈도를 기술한다.

[금지 규칙]
- 단일 순찰 데이터로 인과관계를 주장하지 마라.
  "때문에", "로 인해", "원인은", "영향으로" 사용 금지.
  대신 "함께 관찰됨", "동일 구역에서 확인됨"으로 서술하라.
- 제공되지 않은 수치를 만들어내지 마라.
- undetermined_rate 가 0.30 을 초과하는 구역은 생육 상태에 대한 결론을
  내리지 말고, 재촬영이 필요하다고 기술하라.
- data_completeness.rate 가 0.90 미만이면 데이터 한계를 반드시 명시하라.
- boundary_confidence 가 "low" 인 경우 구역 구분이 추정값임을 명시하라.
- 관측 수(observation count)는 개체 수가 아니다.
  "개체 수", "그루", "포기" 등의 표현을 쓰지 말고 "관측 수"로 서술하라.

[출력]
지정된 JSON 스키마를 따른다. 모든 서술 필드는 한국어로 작성한다.
서술은 간결하게. 각 필드는 지정된 문장 수를 지킨다.
"""
