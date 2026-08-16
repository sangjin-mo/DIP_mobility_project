# Golden fixture — patrol 20260813_1430

Shared test data for all four teams. Producers assert their output matches
these; consumers assert their parser accepts them. Validated against
`contracts/schemas/` in CI.

Deliberately exercises edge cases rather than a happy path:

| Case | Where |
|---|---|
| Packet loss | `seq` 3 is absent — expected 12, received 11, rate 0.917 |
| Null environmental readings | `seq` 5 (sensor read failure, or no sensor fitted) |
| Emergency stop mid-zone | `seq` 6–7, 10 s stopped inside zone 1 |
| Zone boundary after a stop | zone 2 starts at `event_seq` 3, unshifted by the stop |
| Image below quality floor | `z1_003` at 0.31, must be excluded from selection |
| Image 100% 판단불가 | `z1_003` — contributes to zone 1's `undetermined_rate`, which lands exactly at the flag threshold (see below) without crossing it |
| Image with zero detections | `z2_001`, must not divide by zero |
| Transit segment | `seq` 0–1 before the first `ZONE_ENTER`, `zone_id` 0 |

## Expected aggregate

Derived by hand. Any implementation must reproduce these exactly.

- `udp_expected` 12, `udp_received` 11, `rate` 0.9167
- `zone_boundary_confidence` "high"
- Zone 1: temp avg 27.28 (n=5), humidity avg 65.74 (n=5)
- Zone 1 observations: 정상 10, 미성숙 2, 병충해_의심 2, 판단불가 6
- Zone 1 `undetermined_rate` 0.30 — **not** above the 0.30 threshold, so no
  `재촬영_필요` flag. Boundary case, deliberately.
- Zone 1 status **주의** — 병충해_의심 ratio 2/14 = 0.143 is > 0.05 but ≤ 0.15
  (not 이상); an EMERGENCY_STOP also occurred in this zone, which
  independently forces 주의 per spec §6's status rule. Either condition
  alone is sufficient — this fixture deliberately has both, so an
  implementation that gets only one of the two rule branches right still
  produces the correct status here. Don't let that mask a bug: test the two
  branches separately elsewhere, not just against this fixture.
- Zone 2: temp avg 28.1 (n=3), no observations, `undetermined_rate` null

## Regenerating

The emergency stop must not shift the zone 2 boundary. If it does, the
segmentation implementation has reverted to elapsed-time mapping. See ADR-0003.
