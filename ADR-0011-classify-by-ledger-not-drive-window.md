# ADR-0011 — Scope classification by a ledger, not by the patrol's drive window

Date: 2026-08-25
Status: Accepted

Amends the Decision of [ADR-0010](ADR-0010-llm-does-the-analysis.md); that
record's core choice — we classify the images ourselves with a multimodal LLM,
emitting the C2 contract VIS never delivered — is unchanged.

## Context

ADR-0010 ended with: "`web_dashboard`'s `PatrolEventService.end_patrol` spawns
it automatically on STOP, scoped to that patrol's own images by
`--after-ts-ms`/`--before-ts-ms`."

That scoping never selected anything. Every auto-triggered run recorded
`classified 0/0 image(s), wrote _COMPLETE`, and every report produced after the
feature landed had `zones=0, images_analysed=0`. The only report in `reports/`
with real content, `20260820_0930`, predates auto-classification and came from
`devtools/fake_vis.py`.

Two independent reasons, both structural rather than incidental:

1. **The camera does not run on the drive's schedule.**
   `web_dashboard/INTEGRATION_RUNBOOK.md` states it directly: "카메라 촬영과
   차량 제어는 서로 독립적이다. 카메라 버튼은 차량의 START, STOP API를 호출하지
   않는다." Replaying the real windows against `received/2026-08-24/` (146
   frames captured 17:29:47–17:34:16): patrol `0832` covered 17:32:56–17:32:57
   and matched 1 frame; patrols `0837` and `1250` matched none. The drive
   window and the capture window are unrelated intervals that happen to share
   a clock.

2. **The images were not on the PC yet.** Transfer from the webcam Pi is a
   user-initiated action (`/control/request-transfer` → the Pi's
   `/trigger-upload`). Nothing in the STOP path invoked it, so at the moment
   classification started, this patrol's frames were still on the Pi.

A third problem made the failure unrecoverable rather than merely wrong.
`image_id` was `{patrol_id}_{index:03d}` over the sorted source list —
positional, so stable only while the input set was. Re-running after a late
transfer rebound every id to a different picture, and
`Store.insert_analysis`'s `INSERT OR IGNORE` on `(patrol_id, image_id)` then
kept the *stale* row while `shutil.copyfile` overwrote the image on disk.
A stale `_COMPLETE` from the failed run would also let `VisWatcher` return
before the retry had written anything.

## Decision

**Couple the camera to the patrol.** START arms capture on the webcam Pi and
STOP disarms it, pushed at the moment the button is pressed. Capture was
previously gated on the Pi polling `GET /api/control/status` for `RUNNING`,
which reports "the drive process is alive" rather than "a patrol is underway":
in the 2026-08-24 data it ran continuously from 17:29 to 17:34, covering
stretches with no patrol at all, and was off entirely during the 17:35–17:38
patrols. A failure to arm is reported in the START response rather than
discovered later as an empty report.

**Scope by what has not been classified yet, not by when the rover was
driving.** `classify_patrol` classifies every image in `--source-dir` absent
from a ledger at `{data_root}/analysis/.classified.json`, then records what it
did. `--after-ts-ms`/`--before-ts-ms` survive for manual runs that need to
re-examine one span, and now filter on capture time read from the filename
rather than on mtime; the automatic run passes neither.

**Pull the images before classifying them.** `end_patrol` triggers the Pi
transfer and waits for it to finish before spawning `classify.py`. Both run on
a background thread so the STOP button does not block on an upload.

**Derive `image_id` from the source filename.** Re-running is then idempotent:
the same picture always lands under the same id, so `INSERT OR IGNORE` updates
nothing rather than binding a stale row to a rewritten image. Nothing in
`ai_report` parses `image_id` — `devtools/fake_vis.py` already used a
different scheme — so its shape was free.

## Consequences

**The ledger is now load-bearing state.** Deleting it re-classifies, and
re-bills, everything in the directory. It is written atomically for that
reason. `--reclassify` is the deliberate override.

**"Which patrol does this image belong to" is answered by arrival order, not
by timestamps** — and that is now a good answer rather than a resigned one.
When this was drafted the camera ran on its own schedule, so arrival order was
merely the least-wrong option available. The same change set closed that gap:
the dashboard's START arms patrol capture on the webcam Pi and STOP disarms it
(`POST /capture/start` · `/capture/stop`), so the only photos in existence for
a given patrol are the ones taken during it. Fetch-on-STOP then makes arrival
order and patrol membership the same thing.

The residual case is two patrols run without a transfer in between — if the
STOP-time fetch fails, the next patrol's fetch collects both patrols' photos
and attributes them all to itself. The failure is logged at the time it
happens.

**A patrol that captures nothing still produces a report**, as before: zero
zones, rendered fallback, limitation stated. What changed is that this now
means "nothing was captured" rather than "the filter discarded everything".

**`VIS_COMPLETE_TIMEOUT_S` went back up, to 300s.** It now has to cover the
transfer as well as classification, and ADR-0010's reasoning for cutting it to
120s assumed sequential classification of a ~60-frame patrol. The dashboard
can drive the capture interval to `MIN_CAPTURE_INTERVAL_SEC = 0.2`, so a few
minutes of capture is several hundred frames. Timing out early is not graceful
degradation here: it silently truncates the report with no record of which
frames were dropped.

**ADR-0010's "Classification is sequential" no longer holds.** It was made
concurrent in `3fe0751`/`5197054` without that record being updated;
`classify_patrol` now dispatches up to `CLASSIFY_CONCURRENCY` (default 8, in
`config.py`) calls via `asyncio.gather`, with every filesystem call on a
worker thread so they do not stall the shared transport.
