# ADR-0010 — The LLM does the crop analysis, not VIS

Date: 2026-08-25
Status: Accepted

> [!NOTE]
> The Decision's last paragraph — auto-classification "scoped to that patrol's own images by `--after-ts-ms`/`--before-ts-ms`" —
> is amended by [ADR-0011](ADR-0011-classify-by-ledger-not-drive-window.md).
> That scoping selected nothing on the real system. The Consequences section's
> "Classification is sequential" and its 120s `VIS_COMPLETE_TIMEOUT_S` are
> likewise superseded there. Everything else in this record still holds.

## Context

The original architecture had four teams. VIS owned crop detection: it would
run YOLO over captured images and deliver finished analysis JSON against
`contracts/schemas/c2-analysis.schema.json`, writing one file per image into
`data/analysis/{patrol_id}/` and a `_COMPLETE` marker when the patrol's images
were done. `GUIDELINES.md`'s explicit non-goals say so directly: "No YOLO, no
object detection, no image classification. VIS delivers finished analysis JSON.
Consuming it is the entire job."

That never happened. `vision/image_analysis/design/README.md` §2-3 records that
VIS does not run its own crop-detection model — the planned YOLO-World /
Florence-2 work was not delivered — so `data/analysis/{patrol_id}/` was never
written by anyone, and every patrol aggregated zero analysis records. The rest
of the pipeline was complete and tested; it was starved of its only real input.

The same gap had already forced ADR-0009: with no classified crops and no
`ZONE_ENTER` events from `drive_ver2`, zones had nothing to segment by.

## Decision

We classify the images ourselves with a multimodal LLM, in
`vision/image_analysis/system/classify.py`, emitting exactly the C2 contract VIS
was supposed to emit — one `AnalysisResult` JSON per image into
`data/analysis/{patrol_id}/`, then `_COMPLETE`.

The bridge deliberately reuses `ai_report.models.AnalysisResult`/`Detection`
rather than a parallel schema, so its output cannot drift off C2 without a test
failing, and it lives under `vision/` rather than inside `ai_report/` because it
is standing in for VIS's job, not extending ours.

`web_dashboard`'s `PatrolEventService.end_patrol` spawns it automatically on
STOP, scoped to that patrol's own images by `--after-ts-ms`/`--before-ts-ms`.

## Consequences

**The non-goal in `GUIDELINES.md` no longer holds as written.** "No image
classification" described a boundary with a team that shipped nothing. We now do
classification; what we still do not do is run YOLO or any vision model inside
`ai_report/` itself. The contract boundary survives — C2 is still the interface,
still schema-checked — but it is now a boundary between two of our own
processes.

**Hard rule 2 is weaker than it reads.** "A full report must be producible with
the LLM disabled" used to mean: YOLO detections still arrive, Python aggregates
them, and you get a complete report minus the prose. Now that classification is
itself an LLM call, disabling the LLM yields a report with no zones at all — the
fallback renders, but it describes nothing. `test_llm_disabled_uses_fallback_summary_and_states_limitation`
does not catch this, because it hands the renderer a pre-built zone fixture
(`make_aggregate([one_zone()])`) rather than exercising the pipeline end to end.
The rule should be read as "the *rendering* path survives the LLM being off",
which is materially less than it originally promised.

**Hard rule 1 still holds.** The report LLM continues to receive finalized
numbers and write interpretation only; every count and ratio is still computed
in `pipeline/aggregate.py`. What changed is that the observations being counted
are now model-produced rather than YOLO-produced, which belongs in the report's
own data-limitations section, not in the aggregation code.

**`_COMPLETE` is now ours, so the VIS timeout means something different.** It
was a bound on how long to wait for another team's process; it is now a bound on
our own subprocess, which always touches the marker before exiting — even when
it classified zero images. `VIS_COMPLETE_TIMEOUT_S` therefore no longer gates
the happy path at all, and was cut from 600s to 120s: on a short demo track the
useful question is how quickly a failure becomes visible.

**Classification is sequential and now dominates patrol latency.**
`classify_directory` awaits one vision call per image in a `for` loop, and
`capture.py` saves roughly one image per second while the rover is RUNNING. A
60-second patrol therefore produces ~60 images and several minutes of
classification. Reducing `VIS_COMPLETE_TIMEOUT_S` further without making those
calls concurrent would truncate real patrols into empty reports.

**Cost and rate limits are now per image, not per patrol.** One patrol is one
report call plus N classification calls.
