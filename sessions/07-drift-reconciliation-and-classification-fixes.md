# Drift reconciliation, the Pi watchdog, and the timestamp bug that emptied every report

**Commit:** `4fe6611`
**Date:** 2026-08-24 evening → 2026-08-25

A mixed operations-and-correctness session. It began with routine demo prep
(restart the server, bring the webcam Pi up) and ended by finding the reason
every recent patrol produced an empty report — a bug that had nothing to do
with the LLM, the contracts, or any of the other teams.

## Timeline

1. **Restarted the central server.** Nothing was actually running: every
   process on the Mac dated from a 21:45 reboot that killed the previous run
   before `start_central_server.sh`'s cleanup trap fired. Started `ai_report`
   (UDP 9100 / HTTP 9101), the vision `pc_server` (8000), and `web_dashboard`
   (8080) directly rather than through the script, because the script needs
   `sudo` for the firewall and sudo wasn't cached. The firewall turned out to
   be moot — `.venv/bin/python3` was still in the allowlist, left over from
   the run the reboot interrupted.
2. **The webcam Pi (192.168.2.28) dropped off the network mid-session.** It
   answered on 8002 with `capture.py`'s API, then went to 100% packet loss.
   Confirmed it was the Pi and not the observer: its dashboard traffic froze
   at exactly 999 requests while another client kept climbing 988 → 1020.
   `upload_server` (8001) was already down before that, which is why no image
   had reached the Mac since 17:34.
3. **Wrote a network watchdog for the Pi** (`scripts/pi-net-watchdog.sh` +
   `pi-net-watchdog.service`). The user was firm that NetworkManager, not
   power, was the cause; their observation that the Pi "does turn on and it
   does boot" ruled out the brownout hypotheses and put the failure squarely
   in the mode a watchdog can fix.
4. **Audited how well-defined the AI subsystem is versus the other three.**
   AI is the only subsystem with requirement IDs traced to tests, a formal
   error-handling matrix, and JSON Schema contracts. Every schema in
   `contracts/schemas/` sits on an AI boundary; DR↔WEB and VIS↔WEB have none.
5. **Found the documentation split.** Sorting docs by last commit shows a
   frozen tier (`README.md`, `00`, `01`, `03` — last touched 08-16/17,
   describing the four-team world) and a living tier (`02`, `GUIDELINES.md`,
   `CALL_MAP.md`, `04`, `ADR-0009` — 08-23/24, describing what was actually
   built). The drift is not uniform; it is exactly the docs that assumed
   other teams would deliver.
6. **Wrote ADR-0010** for the decision that had never been recorded: the LLM
   does the crop analysis because VIS never shipped its model.
7. **Corrected `04-traceability-matrix.md`** — flagged that ADRs 0001–0008
   are cited as authority but were never committed, and fixed a coverage
   summary claiming 13 untested requirements while every row read ✓.
8. **Cut `VIS_COMPLETE_TIMEOUT_S` from 600s to 120s**, after establishing the
   600s was never a delay on the happy path.
9. **Made classification concurrent** — `classify_patrol` awaited one vision
   call per image in a `for` loop.
10. **Found and fixed the mtime bug** (below), then added frame context to
    the classification prompt.

## The bug that emptied every report

`classify.py` derived each image's capture time from its file mtime. The
upload writes files with a plain `open(...).write(...)`
(`pc_server/routes_upload.py:57`) and never restores the original timestamp,
so every file in one transfer batch carries the moment it landed on the PC:

```
20260824_172947_cam01_001.jpg  capture=17:29:47  mtime=17:32:08  drift=142s
20260824_172948_cam01_001.jpg  capture=17:29:48  mtime=17:32:08  drift=141s
20260824_172949_cam01_001.jpg  capture=17:29:49  mtime=17:32:08  drift=140s
```

`PatrolEventService._trigger_classification` scopes classification to one
patrol with `--after-ts-ms`/`--before-ts-ms`, compared against mtime. Images
are pulled off the Pi *after* STOP, so their mtime always fell outside the
patrol window and **every frame was discarded before a single API call was
made** — "no images found", `_COMPLETE` written, zero analyses, empty report.

The fix parses the capture stamp out of the filename
(`capture.py::make_filename`'s `YYYYMMDD_HHMMSS_cam_seq.jpg`), falling back to
mtime for names that don't carry one. Fixed at the consumer rather than making
the upload restore mtime, since `routes_upload.py` is the vision team's file
and parsing the name needs no change on the Pi.

Confirmed against real data: stored events show patrol `20260824_0834` ended at
17:34:16 and the last captured frame is `20260824_173416`. The images existed
and their timestamps were right; the filter was the only thing throwing them
away.

## Frame context in the classification prompt

The user's own proposal, and the reason the timestamp fix mattered beyond
bookkeeping: tell the model what it is looking at, so a tomato bisected by the
frame edge is not counted as two tomatoes. Each call now sends a text block
before the image giving position in the sweep, real capture time, gap to the
previous frame, and the rule that an edge-cut crop counts once.

This fixes the *within-frame* half of the problem only. The same tomato in
frames 23 and 24 is still two observations, because each call sees exactly one
image and, per ADR-0006, the output stays 관측 수 rather than 개체 수. Real
cross-frame deduplication would need chunked windows with overlap and a merge
step, which changes the C2 contract. `FrameContext`'s docstring says so
explicitly so nobody later mistakes it for dedup.

## Verification

- Full suite: **190 passed** (184 before; +6 new tests).
- New tests pin the things most likely to regress: `image_id` stays bound to
  sorted order when calls finish out of order, the semaphore actually bounds
  in-flight calls, `capture_ts_ms` reads the filename rather than the mtime,
  and an in-window frame survives a much later mtime.
- Checked whether a retroactive report would now produce real output. It would
  not: all 146 images in `received/2026-08-24/` are stop-sign and camera tests
  — a laptop keyboard, a paper cup, a 정지/STOP sign on a monitor. The
  classifier would correctly return zero detections for all of them. The
  plumbing is fixed; the pictures have no crops in them.

## Decisions

| Decision | Rationale |
|---|---|
| Parse capture time from the filename, not mtime | The filename is the only surviving record of when a frame was taken; mtime is transfer time. Fixed at the consumer so the Pi needs no change |
| Filename stamp interpreted in local time | The stamp carries no timezone. Correct while Pi and PC are both KST; a mismatch shifts `captured_at_ms` and the window filter by the offset |
| `VIS_COMPLETE_TIMEOUT_S` 600 → 120, not to zero | `_COMPLETE` already ends the wait the moment classification finishes, so the ceiling never gated the happy path. Completing on STOP would aggregate zero analyses, because `end_patrol` posts `PATROL_END` *before* spawning the classifier |
| Classification concurrency capped at 8 | ~1 capture/sec means a one-minute patrol is ~60 calls; sequential that is minutes of wall clock landing between STOP and the report. 8 stays under the timeout without tripping per-minute rate limits |
| Both the file read and the API call sit inside the semaphore | Building all coroutines eagerly would otherwise load every image into memory at once |
| Frame context added, cross-frame dedup declined | Dedup would make the model produce a number, which hard rule 1 forbids, and would break the per-image C2 schema |
| Watchdog probes the gateway, never the dashboard PC | Probing an application peer reports "down" whenever that machine changes networks, and the watchdog would then bounce a healthy link |
| No `Co-Authored-By` trailer on the commit | Standing instruction from session 06: "I don't want you as a contributer" |

## Known open items (as of 2026-08-25)

- **The watchdog is written but not deployed.** No SSH access to the webcam
  Pi (`publickey,password` denied for both `pi` and `ubuntu`), and the Pi was
  still offline at end of session. Install commands are in the session notes.
- **`upload_server` (8001) on the webcam Pi is still down**, so no new image
  has reached the Mac since 17:34 on 08-24. Everything downstream of that is
  untestable with real data until it is back.
- **ADRs 0001–0008 still do not exist.** Now flagged in `04` and in
  `README.md`'s index rather than silently cited. 0009 and 0010 are the only
  records that exist as files.
- **Hard rule 2 is weaker than it reads** and its guard test does not catch
  it — `test_llm_disabled_uses_fallback_summary_and_states_limitation` hands
  the renderer a pre-built zone fixture instead of exercising the pipeline end
  to end. Documented in ADR-0010's Consequences; not fixed.
- **No CLI path for a retroactive report.** `regenerate` rebuilds from a
  stored `payload.json` and never reclassifies. Building a report from images
  already on disk means running `classify.py` by hand, then POSTing a
  `PATROL_END` for a fresh `patrol_id` to the event API on 9101.
- **A patrol spanning midnight is still unhandled** — its images fall in the
  previous day's `received/` folder and are missed.
