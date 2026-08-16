# 01 — Interface Contracts (ICD)

Human-readable commentary on the machine-readable contracts in `contracts/schemas/`.

**The schema files are the source of truth.** Where this prose and a schema disagree, the schema wins — and the prose is a bug to be fixed. Generate models from the schemas; do not hand-write them from these tables. See [ADR-0008](adr/0008-schema-first-contracts.md).

| Contract | Schema file | Fixture |
|---|---|---|
| C1.1 Telemetry | `contracts/schemas/c1-telemetry.schema.json` | `fixtures/patrol_20260813_1430/telemetry.jsonl` |
| C1.2 Events | `contracts/schemas/c1-event.schema.json` | `fixtures/patrol_20260813_1430/events.jsonl` |
| C2 Analysis | `contracts/schemas/c2-analysis.schema.json` | `fixtures/patrol_20260813_1430/analysis/*.json` |
| C3 Metadata | `contracts/schemas/c3-metadata.schema.json` | — (produced by us) |

## Contract status

Update this table as agreements land. Code against `PROPOSED` contracts, but keep the assumption isolated in a Pydantic model.

| ID | Direction | Transport | Status | Owner to agree with |
|---|---|---|---|---|
| **C1** | DR → AI | UDP + HTTP | `PROPOSED` | DR team |
| **C2** | VIS → AI | filesystem + JSON | `PROPOSED` | VIS team |
| **C3** | AI → WEB | filesystem | `PROPOSED` | WEB team |

> [!FLAG] **Needs human review**
> All three contracts are drafts written by the AI team. Get written sign-off before Phase A2. A schema change after A4 is expensive.

---

## C1 — DR → AI

Two channels with different reliability requirements. **This split is the most important design decision in the ICD.**

### C1.1 Telemetry — UDP, lossy, acceptable

Periodic sensor samples. Individual losses are tolerable because these are aggregated into averages.

- **Transport:** UDP, rover → PC, port `9100` (configurable)
- **Rate:** 1 Hz nominal
- **Payload:** UTF-8 JSON, one object per datagram
- **Size limit:** **1400 bytes maximum.** Larger packets fragment and loss rates rise sharply. Never send images over this channel.

```json
{
  "patrol_id": "20260813_1430",
  "seq": 1042,
  "ts_ms": 1755061800123,
  "type": "TELEMETRY",
  "zone_id": 3,
  "env": {
    "temp_c": 27.4,
    "humid_pct": 68.2
  },
  "drive": {
    "speed_mps": 0.28,
    "steer": -0.11,
    "ultra_cm": 145,
    "state": "RUNNING"
  }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `patrol_id` | string | yes | `YYYYMMDD_HHMM`, constant for the whole patrol |
| `seq` | int | yes | **Monotonic from 0, per patrol.** Used for loss calculation |
| `ts_ms` | int | yes | Unix epoch milliseconds, see §C1.4 |
| `type` | `"TELEMETRY"` | yes | discriminator |
| `zone_id` | int \| null | yes | DR's best guess; **AI does not trust this** — see §C1.2 |
| `env.temp_c` | float \| null | yes | null when the sensor read fails |
| `env.humid_pct` | float \| null | yes | null when the sensor read fails |
| `drive.speed_mps` | float | yes | |
| `drive.steer` | float | yes | −1.0 … 1.0 |
| `drive.ultra_cm` | int | yes | |
| `drive.state` | enum | yes | `RUNNING` \| `STOPPED` \| `EMERGENCY` |

**`seq` is mandatory and non-negotiable.** Without it, packet loss is invisible and the report cannot state its own coverage. If DR pushes back on anything in C1, hold the line on this field.

> [!FLAG] **Needs human review — sensors not present in the PiRacer kit**
>
> The Waveshare PiRacer AI Kit ships with a camera, steering servo, drive motors, OLED, and battery ADC. It has **no temperature/humidity sensor and no ultrasonic sensor.** Three fields above therefore have no hardware behind them today:
>
> | Field | Requirement | If not fitted |
> |---|---|---|
> | `env.temp_c`, `env.humid_pct` | DR FR_101 | Always `null`. The 환경 조건 report section is empty, and AI_101's zone-average requirement is unimplementable |
> | `drive.ultra_cm` | DR FR_102 | Always `null` |
> | `EMERGENCY_STOP` event (C1.2) | DR FR_110 (**Critical**) | Never fires. `drive_events` is always empty and the 통로 장애 요인 section loses half its input |
>
> Confirm with DR whether a DHT22/SHT31 and an HC-SR04 are being added. Both are inexpensive and both are already required by the SRS.
>
> **If they are not fitted:** the AI subsystem still works. Environmental fields degrade to `null`, the aggregate omits `env`, the renderer skips the 환경 조건 section body with a stated reason, and the prompt receives no environment data. Handle this in Phase A2 rather than assuming the fields are populated — write the null path first.

### C1.2 Events — reliable delivery required

Events define the structure of the patrol. **A lost event corrupts the entire report silently.**

- **Transport:** HTTP `POST /api/events` to the AI service on port `9101`
- **Fallback if DR cannot do HTTP:** same JSON over UDP, **sent 3 times**, deduplicated by `(patrol_id, event_seq)`
- **Idempotency:** receiving the same `event_seq` twice must be a no-op

```json
{
  "patrol_id": "20260813_1430",
  "event_seq": 7,
  "ts_ms": 1755061812000,
  "type": "ZONE_ENTER",
  "zone_id": 4,
  "detail": {}
}
```

| `type` | When | `detail` fields |
|---|---|---|
| `PATROL_START` | Drive command accepted | `{"route_id": "..."}` |
| `ZONE_ENTER` | Stop-sign marker recognized (DR FR_113) | — |
| `EMERGENCY_STOP` | Obstacle within 10 cm (DR FR_110) | `{"ultra_cm": 8}` |
| `LINE_LOST` | Line detection failed past threshold (DR FR_105) | `{"duration_ms": 2400}` |
| `PATROL_END` | Route complete or stop command | `{"reason": "completed" \| "user_stop" \| "fault"}` |

#### Why zone boundaries must be events

The AI subsystem assigns every telemetry sample and image to a zone by finding which `ZONE_ENTER`…`ZONE_ENTER` interval its timestamp falls into.

It must **not** derive zones from elapsed time. DR emergency-stops for obstacles (FR_110), stops on line loss (FR_105), and slows on curves (FR_106). Any of these shifts a time-based zone mapping, and every subsequent zone gets mislabelled — producing a report that is confidently and invisibly wrong.

DR already stops at stop signs for FR_113. That stop is the zone boundary. This costs no new hardware.

**Requested new DR requirement:**

> **DR_104 — 구역 경계 이벤트 발행:** 정지 표지판 인식 시 `ZONE_ENTER` 이벤트를 AI 서브시스템에 발행해야 한다.

If DR cannot deliver `ZONE_ENTER`, the AI subsystem falls back to segmenting on `EMERGENCY_STOP`-corrected cumulative distance and marks every zone `boundary_confidence: "low"`, which appears as a stated limitation in the report. It degrades loudly, never silently.

> [!FLAG] **Two DR requirements contradict the PiRacer hardware**
>
> Worth raising with DR — these are their requirements, but both affect our inputs.
>
> **FR_104** specifies line detection by IR 센서 가중치 평균. The PiRacer kit has no IR or grayscale sensor. FR_107–109 describe camera-based detection, so the requirements are internally inconsistent and the hardware settles it: **line following is camera-based.** FR_104 should be rewritten accordingly.
>
> **FR_107–109** specify differential motor control (`L = R`, `L > R`, `L < R`). PiRacer steers with an MG996R servo through steering knuckles and pull bars — Ackermann geometry, not differential drive. These should be rewritten as steering-angle control.
>
> Neither changes our schema. Both change what DR builds, and FR_104's resolution matters to us indirectly: camera-based line following means `ZONE_ENTER` (from stop-sign recognition, FR_113) depends entirely on the vision pipeline. If vision degrades, zone segmentation degrades with it. The fallback path in this section is therefore more likely to be exercised than originally assumed — implement and test it properly, do not treat it as a rare edge case.

### C1.3 Loss accounting

Expected packet count for a patrol is `max(seq) + 1`. Received count is the number of distinct `seq` values stored.

```
loss_rate = 1 - (received / expected)
```

Reported in `metadata.json` and stated in the report when coverage drops below 90%.

### C1.4 Time synchronisation

Image-to-telemetry matching and zone segmentation both depend on comparable timestamps across two machines.

**Preferred:** rover runs NTP against the base station; `ts_ms` is wall-clock epoch ms.

**Fallback if NTP is unavailable:** rover sends its monotonic clock in `ts_ms` and includes `"clock": "monotonic"` in the `PATROL_START` event. The AI subsystem records the offset at `PATROL_START` and applies it to all subsequent timestamps.

> [!FLAG] **Needs human review**
> Confirm with DR which clock mode ships. Assume NTP until told otherwise.

---

## C2 — VIS → AI

VIS captures images, runs YOLO on the PC, and produces structured analysis. **The AI subsystem never runs a model on an image.**

### C2.1 Delivery

- **Images:** JPEG files under `data/images/{patrol_id}/`
- **Analysis:** one JSON file per image, `data/analysis/{patrol_id}/{image_id}.json`
- **Completion signal:** VIS writes `data/analysis/{patrol_id}/_COMPLETE` when all images for the patrol are analysed

The AI pipeline waits for both `PATROL_END` (from DR) and `_COMPLETE` (from VIS) before aggregating. Timeout: 10 minutes, after which it proceeds with whatever exists and records the gap as a limitation.

### C2.2 Analysis result schema

```json
{
  "image_id": "20260813_1430_z3_007",
  "patrol_id": "20260813_1430",
  "captured_at_ms": 1755061800123,
  "image_path": "images/20260813_1430/z3_007.jpg",
  "image_quality": 0.71,
  "detections": [
    {
      "class": "tomato",
      "state": "정상",
      "count": 4,
      "confidence": 0.88
    },
    {
      "class": "tomato",
      "state": "병충해_의심",
      "count": 1,
      "confidence": 0.62
    },
    {
      "class": "tomato",
      "state": "판단불가",
      "count": 2,
      "confidence": null
    }
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `image_id` | string | yes | unique within a patrol |
| `captured_at_ms` | int | yes | same clock basis as C1 |
| `image_path` | string | yes | relative to the data root |
| `image_quality` | float 0–1 | yes | drives image selection; see §8 of the spec |
| `detections[].class` | string | yes | crop type, lowercase ASCII |
| `detections[].state` | enum | yes | `정상` \| `미성숙` \| `병충해_의심` \| `판단불가` |
| `detections[].count` | int | yes | instances of this class+state in this image |
| `detections[].confidence` | float \| null | yes | null permitted only for `판단불가` |

### C2.3 Rules VIS must honour

1. **The state enum is closed.** Exactly the four values above. A fifth value, a typo, or a translated variant breaks aggregation. AI raises on unknown values rather than guessing.
2. **`판단불가` is always reported explicitly.** It must never be silently dropped. It is the denominator of the coverage calculation and drives the `재촬영_필요` flag.
3. **`image_quality` is always present**, even for images with no detections.
4. **No cross-frame deduplication is assumed.** The same physical tomato appearing in three consecutive frames counts three times.

#### Consequence of rule 4 — terminology

Because detections are not deduplicated across frames, the aggregated figure is **observation count**, not plant count. All output uses 관측 수 (observation count), never 개체 수 (individual count).

This is a deliberate honesty choice. Claiming individual counts without tracking would be wrong, and building tracking is out of scope. If VIS later adds deduplication, it announces this per patrol and the terminology can change.

> [!FLAG] **Needs human review**
> Confirm with VIS whether any deduplication happens. If it does, the wording rule above changes.

---

## C3 — AI → WEB

### C3.1 Output layout

```
reports/
└── {patrol_id}/
    ├── report.md        ← WEB parses to HTML (WEB_101)
    ├── metadata.json     ← WEB aggregates for zone status display (WEB_101)
    ├── payload.json      ← exact LLM input, for audit and regeneration
    └── images/           ← selected images referenced by the report
        ├── z3_007.jpg
        └── ...
```

Writes are atomic: build in a temp directory, then rename. WEB must never observe a half-written report.

### C3.2 `report.md` structure guarantee

WEB parses Markdown, so the structure is contractual. **These six H2 sections always exist, always in this order**, for every report including fallbacks.

```markdown
# 순찰 리포트 — {patrol_date}

## 순찰 요약
## 구역별 생육 현황
## 환경 조건
## 통로 장애 요인
## 권장 조치
## 데이터 한계
```

Within 구역별 생육 현황, each zone is an H3: `### {zone_id}구역 — {zone_name}`.

The renderer is a Jinja template, not model output, so this holds unconditionally.

### C3.3 `metadata.json` schema

WEB's zone-status display reads this, not the Markdown.

```json
{
  "patrol_id": "20260813_1430",
  "patrol_date": "2026-08-13",
  "generated_at": "2026-08-13T15:02:11+09:00",
  "duration_min": 18,
  "overall_status": "주의",
  "llm": {
    "enabled": true,
    "model": "gpt-5.6-luna",
    "prompt_version": "v1.2",
    "input_tokens": 15840,
    "output_tokens": 1420,
    "cost_usd": 0.0049
  },
  "data_completeness": {
    "udp_received": 1204,
    "udp_expected": 1240,
    "rate": 0.971,
    "images_analysed": 62,
    "zone_boundary_confidence": "high"
  },
  "zones": [
    {
      "zone_id": 3,
      "zone_name": "B동 2열",
      "status": "주의",
      "env": {
        "temp_c": {"avg": 27.4, "min": 26.1, "max": 29.0, "n": 61},
        "humid_pct": {"avg": 68.2, "min": 64.0, "max": 72.5, "n": 61}
      },
      "observations": {
        "tomato": {"정상": 42, "미성숙": 11, "병충해_의심": 3, "판단불가": 6}
      },
      "undetermined_rate": 0.097,
      "flags": [],
      "image_ids": ["z3_007", "z3_012", "z3_019"],
      "confidence": "high"
    }
  ]
}
```

| Field | Notes for WEB |
|---|---|
| `overall_status` | `정상` \| `주의` \| `이상` — drives the dashboard badge |
| `llm.enabled` | `false` on a fallback report; WEB should indicate reduced content |
| `data_completeness.rate` | below 0.90 warrants a dashboard warning |
| `zones[].flags` | may contain `재촬영_필요`; WEB should surface this |
| `zones[].image_ids` | resolve against `images/{id}.jpg` in the same directory |

### C3.4 What WEB must not assume

- Zone count varies between patrols. Do not hardcode.
- A zone may have zero observations. Render as 관측 없음, not zero-valued charts.
- `report.md` may be a fallback with thinner prose; the six sections still exist.
