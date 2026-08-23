# 04 — Requirements Traceability Matrix

Every requirement traces to a design element, an implementation, and a test. Orphans in either direction are defects: a requirement with no test is unverified, and code with no requirement is scope creep.

Update the Status column as phases complete. This table is what a reviewer checks first.

## Legend

`—` not started · `WIP` in progress · `✓` implemented and tested · `BLOCKED` waiting on another team

---

## AI subsystem requirements (ours)

| Req ID | Requirement | Design | Module | Test | Phase | Status |
|---|---|---|---|---|---|---|
| AI_101.1 | UDP 텔레메트리 수신 | ICD C1.1 | `ingest/udp_listener.py` | `test_udp_ingest`, `test_dedup`, `test_out_of_order` | A1 | ✓ |
| AI_101.2 | 구역별 온습도 평균 산출 | spec §6 | `pipeline/aggregate.py` | `test_env_stats_exclude_null_samples` | A2 | ✓ |
| AI_101.3 | 순찰 데이터 집계 및 JSON 패키징 | spec §8 | `pipeline/payload.py` | `test_estimate_tokens_matches_spec_formula`, `test_over_budget_degrades_to_two_images_per_zone` | A4 | ✓ |
| AI_101.4 † | 이미지 선정 기준 정의 | spec §7 | `pipeline/select_images.py` | `test_anomaly_exemplar_picked_first_highest_quality_among_disease_images`, `test_quality_floor_excludes_low_quality_images_entirely` | A4 | ✓ |
| AI_101.5 † | 구역 경계 판정 | ADR-0003, ICD C1.2 | `pipeline/segment.py` | `test_emergency_stop_does_not_shift_boundary`, `test_fallback_segmentation_when_no_zone_enter_events` | A2 | ✓ |
| AI_101.6 † | 데이터 완전성 측정 | ICD C1.3 | `ingest/store.py` | `test_loss_rate_with_gap`, `test_loss_rate_none_when_empty` | A1 | ✓ |
| AI_102.1 | AI 리포트 생성 요청 | spec §9 | `llm/client.py` | `test_happy_path_returns_output_and_metadata` | A5 | ✓ |
| AI_102.2 | 분석 시스템 프롬프트 정의 | spec §9.2 | `llm/prompts.py` | `test_scan_prohibited_language_detects_causal_connectors`, `test_scan_prohibited_language_detects_plant_count_words` | A5 | ✓ |
| AI_102.3 | 리포트 및 메타데이터 저장 | ICD C3.1 | `storage/layout.py` | `test_no_partial_directory_is_ever_observable_at_the_final_path`, `test_a_failed_write_does_not_touch_an_existing_report` | A3 | ✓ |
| AI_102.4 † | 리포트 생성 실패 처리 | spec §12 | `llm/client.py` | `test_retries_exhausted_falls_back_gracefully`, `test_no_retry_on_bad_request`, `test_schema_invalid_response_falls_back_gracefully` | A5 | ✓ |
| AI_102.5 † | 프롬프트·모델 버전 기록 | ICD C3.3 | `llm/client.py` | `test_happy_path_returns_output_and_metadata`, `test_system_prompt_matches_pinned_hash` | A5/A6 | ✓ |
| AI_103 † | 리포트 재생성 | spec §11 | `cli.py` | `test_regenerate_produces_a_different_report_with_no_data_root`, `test_regenerate_carries_images_forward` | A6 | ✓ |
| AI_104 † | 회차 간 비교 | — | — | — | A7 | deferred |

† Added during design review; not in the original requirements list. See `02-ai-subsystem-spec.md` and the ADRs for rationale.

---

## Derived requirements — design decisions with no source requirement

These emerged from ADRs. They are real requirements and need tests, but no customer requirement generated them.

| Derived ID | Requirement | Source | Test | Phase | Status |
|---|---|---|---|---|---|
| D_001 | The LLM must not compute or restate numbers | ADR-0004 | `test_schema_has_no_numeric_fields_except_zone_id` | A5 | ✓ — enforced structurally by the output schema, not a runtime scan |
| D_002 | Aggregation must be deterministic | ADR-0004 | `test_aggregate_is_deterministic` | A2 | ✓ |
| D_003 | A complete report must be producible with the LLM disabled | ADR-0004 | `test_llm_disabled_uses_fallback_summary_and_states_limitation` | A3 | ✓ |
| D_004 | Report Markdown must always contain six H2 sections in order | ADR-0005, ICD C3.2 | `test_six_sections_present_and_ordered`, `test_six_sections_present_with_zero_zones` | A3 | ✓ |
| D_005 | Unknown VIS state enum values must raise, never coerce | ICD C2.3 | `test_unknown_state_raises` (ingest), `test_unknown_vis_state_raises_rather_than_coerces` (aggregate) | A2 | ✓ |
| D_006 | Output must use 관측 수, never 개체 수 | ADR-0006 | `test_scan_prohibited_language_detects_plant_count_words` | A5 | ✓ |
| D_007 | Fixtures must validate against contract schemas | ADR-0008 | `contracts/validate.py` in CI | A0 | WIP — script exists and passes, but `contracts/fixtures/patrol_20260813_1430/` has no actual data files yet, only a README of hand-computed expected values |
| D_008 | A fresh patrol must trigger the full report pipeline automatically on `PATROL_END`, with no cron/scheduling (GUIDELINES.md: "We react to PATROL_END") | `ai_report/CALL_MAP.md`'s "What's wired up now" | `orchestration.py`, `cli.py::_make_patrol_end_trigger`, `ingest/event_api.py`, `ingest/udp_listener.py` | `test_full_pipeline_writes_a_complete_report`, `test_proceeds_without_vis_complete_after_timeout`, `test_unknown_vis_state_is_caught_and_returns_none` (`tests/test_orchestration.py`); `test_patrol_end_fires_on_patrol_end_callback`, `test_patrol_end_callback_not_fired_twice_for_a_duplicate_delivery` (`tests/test_event_api.py`); `test_patrol_end_fires_on_patrol_end_callback_via_udp_fallback` (`tests/test_udp_listener.py`) | A2 | ✓ |

---

## Upstream dependencies — requirements we consume

We do not implement these, but our subsystem fails without them. Track them; escalate when blocked.

| Req ID | Owner | What we need | Our dependency | Status |
|---|---|---|---|---|
| DR FR_101 | DR | 온습도 센서 데이터 | `env.temp_c`, `env.humid_pct`; 환경 조건 section | **BLOCKED — no sensor in the PiRacer kit** |
| DR FR_102 | DR | 초음파 센서 데이터 | `drive.ultra_cm` | **BLOCKED — no sensor in the PiRacer kit** |
| DR FR_110 | DR | 비상정지 | `EMERGENCY_STOP` events; 통로 장애 요인 section | **BLOCKED — depends on FR_102** |
| DR FR_113 | DR | 정지 표지판 감지 | Basis for `ZONE_ENTER` | At risk — camera-only on PiRacer |
| **DR_104** ‡ | DR | 구역 경계 이벤트 발행 | **Zone segmentation correctness** | Agreed 2026-08-13 — `ZONE_ENTER` is a defined `c1-event.schema.json` type; still the single most important field to verify once real DR hardware exists |
| VIS_103 | VIS | 농작물 상태 판단 | `detections[].state` | Agreed 2026-08-13 — `contracts/schemas/c2-analysis.schema.json` |
| VIS_104 | VIS | 분석 결과 생성 | Analysis JSON per image | Agreed 2026-08-13 — `contracts/schemas/c2-analysis.schema.json` |
| VIS_104.4 | VIS | 판단불가 처리 | Coverage denominator, `재촬영_필요` flag | Agreed 2026-08-13 — `contracts/schemas/c2-analysis.schema.json` |

‡ New requirement we are requesting. See ADR-0003.

---

## Downstream consumers

| Req ID | Owner | What they need from us | Contract | Status |
|---|---|---|---|---|
| WEB_101.2 | WEB | Markdown report to render | C3.2, six fixed sections | Agreed 2026-08-13 |
| WEB_101.3 | WEB | 구역별 생육 현황 data | C3.3 `metadata.json` | Agreed 2026-08-13 — `contracts/schemas/c3-metadata.schema.json` (no producer yet; lands in A2) |
| WEB_104.1 | WEB | 농작물 대시보드 data | C3.3 `zones[]` | Agreed 2026-08-13 — `contracts/schemas/c3-metadata.schema.json` |

---

## Known requirement defects

Raised during design review. These belong to other teams but affect our inputs.

| Req ID | Defect | Recommended fix | Raised |
|---|---|---|---|
| DR FR_104 | Specifies IR 센서 가중치 평균; the PiRacer kit has no IR sensor. Contradicts FR_107–109, which describe camera-based detection | Rewrite as camera-based line detection | ADR-0007 |
| DR FR_107–109 | Specify differential motor control (`L > R`, `L < R`); PiRacer uses Ackermann servo steering | Rewrite as steering-angle control | ADR-0007 |
| AI_101.2 | Specifies 타임코드-구역 매핑, which breaks under FR_105/FR_110/FR_106 | Event-based segmentation | ADR-0003 |
| AI_102.2 | Requires 환경 데이터 상관 분석; a single patrol yields ~6 data points, insufficient for any correlation claim | Constrain prompt to co-observation language; defer real trends to AI_104 | spec §9.2 |
| AI_102.1 | Specifies receiving Markdown; unstable structure breaks WEB's parser | Structured JSON output, render Markdown locally | ADR-0005 |

## Coverage summary

| Category | Count | Traced | Untested |
|---|---|---|---|
| AI requirements | 13 | 13 | 13 (pre-implementation) |
| Derived requirements | 7 | 7 | 7 |
| Upstream dependencies | 8 | — | 4 blocked |
| Requirement defects | 5 | — | 5 open |
