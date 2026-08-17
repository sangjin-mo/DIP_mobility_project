# Original orientation + A1 implementation prompt

Read GUIDELINES.md, then docs/00 through docs/03, before doing anything else.

I own the AI subsystem of a four-team project. The other three subsystems
(DR/driving, VIS/YOLO, WEB/dashboard) belong to teammates and are listed as
explicit non-goals in GUIDELINES.md. Do not implement them.

We are starting at Phase A1 in docs/03-build-plan.md. Phase A0 (contract
sign-off) is a human task already in progress — code against the contracts as
written, but keep every boundary assumption isolated in a Pydantic model.

STEP 1 — orientation. Do not write code yet. Report back with:
  a. Your understanding of the ownership boundary in one paragraph.
  b. The seven hard rules from GUIDELINES.md, restated in your own words.
  c. Any contradiction, ambiguity, or gap you found across the four docs.
     I would rather find doc bugs now than in week three.
  d. A concrete file-by-file plan for A1: which modules, which order,
     which tests.

Then stop and wait for my approval.

STEP 2 — after I approve, implement A1 only:
  - ai_report/ package scaffold, pyproject, config.py, models.py
  - ingest/udp_listener.py, ingest/event_api.py, ingest/vis_watcher.py,
    ingest/store.py
  - devtools/fake_rover.py and devtools/fake_vis.py
  - tests covering every acceptance criterion listed under A1

Constraints:
  - Build the fake emitters alongside the listeners, not after. Every later
    phase is developed against synthetic data, and I will not have rover
    hardware for weeks.
  - No network calls in any test.
  - Thresholds go in config.py, never inline.
  - Stop at the A1 boundary. Do not start segmentation or aggregation.

A1 is done when fake_rover.py can replay a 20-minute synthetic patrol over
real UDP to localhost, including deliberate packet loss, and the computed
loss rate matches the emitter's configured drop rate within 1%.

Once you're done, save all the .md files and this prompt into a "Source Docs"
folder.
