# Call map

Which function calls which, and who runs each module. Covers A1
(`ingest/` + `devtools/`), A2 (`pipeline/segment.py`, `pipeline/aggregate.py`),
A3 (`render/` + `storage/`), A4 (`pipeline/select_images.py`,
`pipeline/payload.py`), A5 (`llm/`), and A6 (`cli.py regenerate`). See
`../02-ai-subsystem-spec.md` §2 for the intended full module layout and
`../01-interface-contracts.md` for the contracts these modules implement.

**There is still no production orchestration for a *fresh* patrol.**
Nothing in this codebase yet calls `segment_patrol` → `aggregate` →
`apply_image_selection` → `build_payload` → `llm.client.generate_report` →
`render_report` → `write_report` automatically when a patrol finishes
(`PATROL_END` + VIS `_COMPLETE`) — that glue is a later addition (likely
`cli.py`, once the trigger condition needs watching for). `cli.py
regenerate {patrol_id}` (A6) *is* fully wired production code, but it's a
different, narrower path — see "The regenerate path" below — that only
works for a patrol whose `payload.json` already exists from some earlier
run. Today the fresh-patrol chain is only exercised by tests and by manual
scripts, such as the one used to smoke-test A2–A5 end to end against a
real running server (with a mocked OpenAI client — CLAUDE.md's API key is
never touched by anything in this repo's own test/smoke tooling). See
"What's not wired up yet" at the bottom.

## Three runtime processes, one shared package

1. **The real server** — `ai_report.cli` (`ai-report serve` / `python -m
   ai_report.cli serve`). Long-running: listens for real DR traffic.
2. **The test/devtool path** — `devtools/fake_rover.py` and
   `devtools/fake_vis.py` run as short-lived scripts that either talk to a
   running server over the network, or (in tests) drive the same listener
   objects in-process on an ephemeral port.
3. **`ai-report regenerate {patrol_id}`** (A6) — a short-lived command that
   rebuilds one report from its own stored `payload.json` + `images/`. See
   "The regenerate path" below; it shares no runtime state with 1 or 2 —
   no `Store`, no UDP/HTTP listener, no `Settings.DATA_ROOT` access at all.

```mermaid
flowchart TB
    subgraph CLI["cli.py — ai-report serve"]
        MAIN["main()"] --> SERVE["_serve(host)"]
    end

    subgraph FAKE_DR["devtools/fake_rover.py — python -m ai_report.devtools.fake_rover"]
        FRMAIN["main()"] --> FRPLAN["generate_patrol_plan()"]
        FRMAIN --> FRDROP["choose_drop_indices()"]
        FRMAIN --> FRREPLAY["replay()"]
        FRREPLAY --> FRHTTP["_post_event_http()"]
    end

    subgraph FAKE_VIS["devtools/fake_vis.py — python -m ai_report.devtools.fake_vis"]
        FVMAIN["main()"] --> FVGEN["generate_analysis_results()"]
        FVMAIN --> FVWRITE["write_analysis_files()"]
    end

    SERVE --> GETSET1["config.get_settings()"]
    SERVE --> STORE1["ingest.store.Store(...)"]
    SERVE --> UDPCREATE["ingest.udp_listener.create_udp_listener()"]
    SERVE --> APPCREATE["ingest.event_api.create_app()"]
    APPCREATE -->|"mounted in"| UVICORN["uvicorn.Server.serve()"]

    FRREPLAY -- "UDP :9100\ntelemetry, +events if --udp-fallback" --> UDPPROTO["udp_listener.TelemetryUDPProtocol"]
    FRHTTP -- "HTTP POST :9101\n/api/events (default)" --> POSTEVENT["event_api.post_event()"]

    UDPCREATE -.creates.-> UDPPROTO
    UDPPROTO --> STOREW1["Store.insert_telemetry() / insert_event()"]
    POSTEVENT --> STOREW2["Store.insert_event()"]

    FVWRITE -- "writes JSON files to\ndata/analysis/{patrol_id}/" --> DISK[("filesystem")]
    DISK -. "read by (A2 orchestration,\ncurrently exercised only in tests)" .-> WATCHER["vis_watcher.VisWatcher.scan_once()"]
    WATCHER --> STOREW3["Store.insert_analysis()"]

    STOREW1 --> DB[("SQLite: sessions.db")]
    STOREW2 --> DB
    STOREW3 --> DB
```

## The A2–A5 pipeline (no automatic trigger yet)

Given a `patrol_id`, this is the chain that turns ingested rows into a
report on disk. Every arrow below is a real function call once something
invokes `segment_patrol`; nothing currently does that automatically.

```mermaid
flowchart LR
    LOAD["caller loads rows via\nStore.telemetry_for_patrol() /\nevents_for_patrol() /\nanalysis_for_patrol()"]
    LOAD --> SEG["pipeline.segment.segment_patrol()"]
    SEG -->|"PatrolSegmentation"| AGG["pipeline.aggregate.aggregate()"]
    AGG -->|"PatrolAggregate\n(image_ids all [])"| SEL["pipeline.select_images.apply_image_selection()"]
    SEG -.->|"segmentation also passed in"| SEL
    SEL -->|"PatrolAggregate\n(image_ids populated)"| PAY["pipeline.payload.build_payload()"]
    SEG -.->|"segmentation also passed in\n(for obstructions)"| PAY
    SEL -.->|"load_selected_images()"| IMGBYTES["images: dict[str, bytes]"]
    PAY -->|"Payload"| LLM["llm.client.generate_report()"]
    IMGBYTES -.-> LLM
    LLM -->|"LlmReportOutput | None,\nLlmMetadata"| REN["render.markdown.render_report()"]
    SEL -->|"PatrolAggregate\n(with llm= merged in)"| REN
    SEG -.->|"segmentation also passed in\n(for obstruction_counts)"| REN
    REN -->|"Markdown string"| ST["storage.layout.write_report()"]
    SEL -->|"PatrolAggregate\n(for metadata.json)"| ST
    SEL -.->|"extra_writers=[...]"| COPY["select_images.copy_and_resize_images()"]
    PAY -.->|"extra_writers=[...]"| WPAY["payload.write_payload()"]
    COPY -.-> ST
    WPAY -.-> ST
    ST --> FS[("reports/{patrol_id}/\nreport.md + metadata.json\n+ payload.json + images/")]

    classDef det stroke:#10B981,stroke-width:2px
    classDef net stroke:#EF4444,stroke-width:2px
    class SEG,AGG,SEL,PAY,REN,ST det
    class LLM net
```

Green stages match spec §1's architecture diagram — deterministic,
network-free. Red is the one stage that touches the network at all
(`llm.client.generate_report` — everything else in this diagram, including
every test that exercises it, never makes a real call). Three non-obvious
wiring details:

- `render_report` takes `obstructions: dict[int, dict[str, int]]`, not a
  `PatrolSegmentation` — it originally took the whole segmentation object
  just to call `.obstruction_counts()` on it, until A6's `regenerate`
  needed to call `render_report` with no segmentation available at all
  (only a stored `Payload`, whose `.obstructions` field is already this
  exact dict). Narrowing the parameter to what the function actually uses
  is what made regeneration possible without a bigger rework.
- `copy_and_resize_images`/`write_payload` must be passed to
  `write_report` via `extra_writers`, **not** called against the final
  report path directly — a real bug (images silently discarded by the
  atomic swap) was found this way during A4's end-to-end smoke test; see
  the `[!FLAG]` in `storage/layout.py`. `cli.py::_write_images` (A6) is the
  same pattern again, for `_regenerate`'s reloaded image bytes.
- `generate_report`'s `LlmMetadata` return value must be merged into
  `PatrolAggregate.llm` (e.g. `agg.model_copy(update={"llm": llm_metadata})`)
  *before* `write_report` runs, so `metadata.json` reflects the real
  `enabled`/`model`/token/cost figures instead of `aggregate()`'s original
  `LlmMetadata(enabled=False)` placeholder. `render_report`'s `llm=`
  argument takes the *content* (`LlmReportOutput | None`) separately —
  the two are threaded through independently because one goes into
  `metadata.json` and the other only ever appears in `report.md`.

## The regenerate path (A6, fully wired)

Unlike the A2–A5 chain above, this one *is* real production code, callable
today: `ai-report regenerate {patrol_id}`. It reconstructs everything from
a previously-written `payload.json` and that report's own `images/`
directory — no `Store`, no `Settings.DATA_ROOT`, no segmentation.

```mermaid
flowchart LR
    CLI["cli.py::_regenerate(patrol_id, report_root)"]
    CLI --> LOADP["payload.load_payload()"]
    LOADP -->|"Payload"| TOAGG["payload.payload_to_aggregate()"]
    CLI --> LOADI["cli._load_report_images()\n(reads report_dir/images/*.jpg)"]
    TOAGG -->|"PatrolAggregate\n(llm=disabled placeholder)"| GEN["llm.client.generate_report()"]
    LOADI -->|"images: dict[str, bytes]"| GEN
    GEN -->|"LlmReportOutput | None,\nLlmMetadata"| REN["render.markdown.render_report()"]
    TOAGG -->|"PatrolAggregate\n(llm= merged in)"| REN
    LOADP -->|"Payload.obstructions"| REN
    REN -->|"Markdown string"| ST["storage.layout.write_report()"]
    TOAGG -->|"PatrolAggregate"| ST
    ST -.->|"extra_writers=[...]"| WPAY2["payload.write_payload()"]
    LOADI -.->|"extra_writers=[...]"| WIMG["cli._write_images()"]
    ST --> FS2[("reports/{patrol_id}/ — same four files,\nnow from a fresh LLM call")]

    classDef det stroke:#10B981,stroke-width:2px
    classDef net stroke:#EF4444,stroke-width:2px
    class LOADP,TOAGG,LOADI,REN,ST det
    class GEN net
```

Proven, not just designed this way: an end-to-end smoke test deleted
`data/` entirely (the directory the original ingest/pipeline run needed)
and ran `ai-report regenerate 20260813_1430` as a real subprocess against
only the report directory — it produced a complete, valid report. The
first version of `_regenerate` had the same images-discarded-by-the-atomic-
swap bug as A4's `copy_and_resize_images`, independently — `_load_report_images`
read the old `images/` directory into memory but nothing wrote it back
into the new one, until `_write_images` was added as another
`extra_writers` entry. Two different call sites, same underlying mistake,
caught the same way both times: by an end-to-end smoke test, not a unit test.

## Per-module function reference

Each row: what the function does in one line, what it calls, and what
calls it. "Framework callback" means asyncio/FastAPI invokes it — nothing
in this codebase calls it directly.

### `config.py`

| Function | Calls | Called by |
|---|---|---|
| `Settings.sqlite_path` (property) | — | `cli.py::_serve` |
| `get_settings()` | `Settings()` | `cli.py::_serve`, `devtools/fake_rover.py::main`, `devtools/fake_vis.py::main` |

### `models.py`

Not "called" so much as constructed/parsed. See the module docstring in
`models.py` for the full producer/consumer list per model. Summary:

| Model / function | Constructed by | Parsed by |
|---|---|---|
| `TelemetryPacket`, `EnvReading`, `DriveReading` | `devtools/fake_rover.py::generate_patrol_plan` | `ingest/udp_listener.py::TelemetryUDPProtocol._handle` |
| `EventMessage` | `devtools/fake_rover.py::generate_patrol_plan`; `ingest/store.py::Store.events_for_patrol` (from DB rows) | `ingest/udp_listener.py::_handle` (UDP fallback); `ingest/event_api.py::post_event` (FastAPI, HTTP body) |
| `AnalysisResult`, `Detection` | `devtools/fake_vis.py::generate_analysis_results` | `ingest/vis_watcher.py::VisWatcher.scan_once` |
| `_validate_patrol_id` | — | Pydantic calls it automatically for every `patrol_id` field |
| `Detection._confidence_required_unless_undetermined` | — | Pydantic calls it automatically after `Detection` validation |

### `ingest/store.py`

| Function | Calls | Called by |
|---|---|---|
| `Store.__init__` | `sqlite3.connect`, executes `_SCHEMA` | `cli.py::_serve`; every test via `tests/conftest.py::store` |
| `Store.close` | — | `cli.py::_serve` (`finally`); test teardown |
| `Store.insert_telemetry` | — | `udp_listener.py::TelemetryUDPProtocol._handle` |
| `Store.insert_event` | — | `udp_listener.py::_handle` (UDP fallback); `event_api.py::post_event` (HTTP) |
| `Store.insert_analysis` | — | `vis_watcher.py::VisWatcher.scan_once` |
| `Store.received_telemetry_seqs` | — | `Store.loss_rate`; tests |
| `Store.max_telemetry_seq` | — | `Store.loss_rate` |
| `Store.loss_rate` | `max_telemetry_seq`, `received_telemetry_seqs` | `tests/test_a1_acceptance.py` (the phase-done check) |
| `Store.events_for_patrol` | — | tests; whatever loads a patrol for `pipeline/segment.py::segment_patrol` |
| `Store.analysis_count` | — | tests |
| `Store.telemetry_for_patrol` | — | tests; whatever loads a patrol for `pipeline/segment.py::segment_patrol` |
| `Store.analysis_for_patrol` | — | tests; whatever loads a patrol for `pipeline/segment.py::segment_patrol` |

### `ingest/udp_listener.py`

| Function | Calls | Called by |
|---|---|---|
| `TelemetryUDPProtocol.__init__` | — | the factory lambda inside `create_udp_listener` |
| `.connection_made` | — | asyncio (framework callback) |
| `.datagram_received` | `._handle` | asyncio (framework callback) |
| `.error_received` | — | asyncio (framework callback) |
| `._handle` | `._parse_json`, `TelemetryPacket.model_validate`, `EventMessage.model_validate`, `Store.insert_telemetry`, `Store.insert_event` | `.datagram_received` |
| `._parse_json` | — | `._handle` |
| `create_udp_listener` | `loop.create_datagram_endpoint` | `cli.py::_serve`; `tests/test_udp_listener.py`; `tests/test_a1_acceptance.py` |

### `ingest/event_api.py`

| Function | Calls | Called by |
|---|---|---|
| `create_app` | `FastAPI()` | `cli.py::_serve`; `tests/test_event_api.py` |
| `post_event` (inner handler) | `Store.insert_event` | FastAPI routing (framework callback), triggered by `devtools/fake_rover.py::_post_event_http` in real use or `TestClient` in tests |

### `ingest/vis_watcher.py`

| Function | Calls | Called by |
|---|---|---|
| `VisWatcher.__init__` | — | tests; intended future A2 orchestration |
| `.scan_once` | `AnalysisResult.model_validate`, `Store.insert_analysis` | `.watch`; tests directly |
| `.watch` | `.scan_once` | tests directly; intended future A2 orchestration (on `PATROL_END`) |

### `cli.py`

| Function | Calls | Called by |
|---|---|---|
| `_serve` | `get_settings`, `Store`, `create_udp_listener`, `create_app`, `uvicorn.Server.serve` | `main` |
| `_write_images` | — | passed to `storage/layout.py::write_report` via `extra_writers`, from `_regenerate` |
| `_load_report_images` | — | `_regenerate` |
| `_regenerate` | `pipeline/payload.py::load_payload`/`payload_to_aggregate`, `_load_report_images`, `llm/client.py::generate_report`, `render/markdown.py::render_report`, `storage/layout.py::write_report`, `pipeline/payload.py::write_payload`, `_write_images` | `main`; directly by `tests/test_cli_regenerate.py` |
| `main` | `_serve` or `_regenerate` (via `asyncio.run`, depending on subcommand) | the `ai-report` console script; `if __name__ == "__main__"` |

### `devtools/fake_rover.py`

| Function | Calls | Called by |
|---|---|---|
| `generate_patrol_plan` | constructs `EventMessage`/`TelemetryPacket`/`EnvReading`/`DriveReading` | `main`; `tests/test_fake_rover.py`; `tests/test_a1_acceptance.py` |
| `choose_drop_indices` | — | `main`; `tests/test_fake_rover.py`; `tests/test_a1_acceptance.py` |
| `_post_event_http` | `urllib.request.urlopen` | `replay` (when `udp_fallback=False`) |
| `replay` | `_post_event_http`, `socket.sendto` | `main`; `tests/test_a1_acceptance.py` |
| `build_arg_parser` | — | `main` |
| `main` | `build_arg_parser`, `get_settings`, `generate_patrol_plan`, `choose_drop_indices`, `replay` | `python -m ai_report.devtools.fake_rover` |

### `devtools/fake_vis.py`

| Function | Calls | Called by |
|---|---|---|
| `generate_analysis_results` | constructs `Detection`/`AnalysisResult` | `main`; `tests/test_fake_vis.py` |
| `write_analysis_files` | — | `main`; `tests/test_fake_vis.py` |
| `build_arg_parser` | — | `main` |
| `main` | `build_arg_parser`, `get_settings`, `generate_analysis_results`, `write_analysis_files` | `python -m ai_report.devtools.fake_vis` |

### `pipeline/segment.py`

| Function | Calls | Called by |
|---|---|---|
| `segment_patrol` | `_first_ts`, `_last_ts`, `_boundaries_from_events` or `_boundaries_from_distance`, `_build_windows` | `tests/test_segment.py`; whatever loads a patrol (A2 orchestration, not yet written) |
| `_first_ts` / `_last_ts` | — | `segment_patrol` |
| `_boundaries_from_events` | — | `segment_patrol`, when any `ZONE_ENTER` events exist |
| `_boundaries_from_distance` | — | `segment_patrol`, fallback path; also called directly by `tests/test_segment.py` to unit-test the distance-integration mechanic in isolation |
| `_build_windows` | `_fill_window` | `segment_patrol` |
| `_fill_window` | — | `_build_windows` |
| `PatrolSegmentation.zones` | — | `pipeline/aggregate.py::aggregate`; `PatrolSegmentation.obstruction_counts`; `pipeline/select_images.py::apply_image_selection`/`copy_and_resize_images` |
| `PatrolSegmentation.obstruction_counts` | `.zones` | `render/markdown.py::render_report`; `pipeline/payload.py::build_payload` |

### `pipeline/aggregate.py`

| Function | Calls | Called by |
|---|---|---|
| `aggregate` | `_aggregate_zone`, `_worst_status`, `_patrol_date`; constructs `PatrolAggregate`/`DataCompleteness`/`LlmMetadata` | `tests/test_aggregate.py`; whatever runs the pipeline for a patrol (A2 orchestration, not yet written) |
| `_patrol_date` | — | `aggregate` |
| `_worst_status` | — | `aggregate` |
| `_stat` | — | `_aggregate_zone` |
| `_aggregate_zone` | `_stat`; constructs `ZoneMetadata`/`ZoneEnv` | `aggregate`, once per non-transit `ZoneWindow` |

### `render/markdown.py`

| Function | Calls | Called by |
|---|---|---|
| `_env_line` | — | `_build_zone_views` |
| `_observation_lines` | — | `_build_zone_views` |
| `_obstruction_line` | — | `_build_zone_views` |
| `_recommendation_line` | — | `_build_zone_views` |
| `_image_note` | — | `_build_zone_views` |
| `_env_note_suffix` | — | `_build_zone_views` |
| `_build_zone_views` | `_env_line`, `_observation_lines`, `_obstruction_line`, `_recommendation_line`, `_image_note`, `_env_note_suffix` | `render_report` |
| `_llm_recommendation_lines` | — | `render_report` |
| `_jinja_env` | `jinja2.Environment(...)` | `render_report` |
| `render_report` | `_build_zone_views`, `_llm_recommendation_lines`, `_jinja_env`, renders `templates/report.md.j2` | `tests/test_markdown.py`; `cli.py::_regenerate` (A6, real production caller); the fresh-patrol chain's caller doesn't exist yet |

### `pipeline/select_images.py`

| Function | Calls | Called by |
|---|---|---|
| `_count_state` / `_has_state` | — | `select_images_for_zone` |
| `select_images_for_zone` | `_count_state`, `_has_state`, `statistics.median` | `apply_image_selection`; directly by `tests/test_select_images.py` |
| `apply_image_selection` | `select_images_for_zone`, `PatrolSegmentation.zones` | whatever runs the pipeline for a patrol; `tests/test_select_images.py` |
| `_resize_image_bytes` | `PIL.Image.open`/`.thumbnail`/`.save` | `copy_and_resize_images`, `load_selected_images` |
| `_selected_image_sources` | `PatrolSegmentation.zones` | `copy_and_resize_images`, `load_selected_images` |
| `copy_and_resize_images` | `_selected_image_sources`, `_resize_image_bytes` | passed to `storage/layout.py::write_report` via `extra_writers`; directly by `tests/test_select_images.py` |
| `load_selected_images` | `_selected_image_sources`, `_resize_image_bytes` | passed to `llm/client.py::generate_report` as its `images` argument; directly by `tests/test_select_images.py` |

### `pipeline/payload.py`

| Function | Calls | Called by |
|---|---|---|
| `estimate_tokens` | — | `build_payload`, once per candidate image budget |
| `_truncate_images` | — | `build_payload` |
| `build_payload` | `PatrolSegmentation.obstruction_counts`, `estimate_tokens`, `_truncate_images`; constructs `Payload` | whatever runs the pipeline for a patrol; `tests/test_payload.py` |
| `write_payload` | — | passed to `storage/layout.py::write_report` via `extra_writers`; directly by `tests/test_payload.py` |
| `load_payload` | `Payload.model_validate_json` | `cli.py::_regenerate`; directly by `tests/test_payload.py` |
| `payload_to_aggregate` | constructs `PatrolAggregate` | `cli.py::_regenerate`; directly by `tests/test_payload.py` |

### `storage/layout.py`

| Function | Calls | Called by |
|---|---|---|
| `_write_files` | writes `report.md`, `metadata.json` into a tmp dir | `write_report` (and monkeypatched directly by `tests/test_layout.py` to simulate a write failure mid-way) |
| `write_report` | `_write_files`, each function in `extra_writers` (e.g. `select_images.copy_and_resize_images`, `payload.write_payload`), `os.replace` (the atomic directory swap) | `tests/test_layout.py`; production orchestration once it exists |

### `llm/schema.py`

| Function | Calls | Called by |
|---|---|---|
| `_make_strict` | recurses into `$defs`/`properties`/`items` | `output_json_schema` |
| `output_json_schema` | `LlmReportOutput.model_json_schema()`, `_make_strict` | `llm/client.py::_call_with_retry` (built once per call, passed as `response_format`); directly by `tests/test_llm_schema.py` |

`LlmReportOutput`/`ZoneNote` themselves are constructed by
`LlmReportOutput.model_validate_json(...)` inside `llm/client.py::generate_report`
(parsing the API response) — see `models.py`-style "Constructed by / Parsed
by" framing in that module's own docstring.

### `llm/prompts.py`

`SYSTEM_PROMPT` is a plain string constant, not a function — read directly
by `llm/client.py::_build_messages` as the API request's system message.

### `llm/client.py`

| Function | Calls | Called by |
|---|---|---|
| `_build_messages` | `json.dumps`, `base64.b64encode` | `generate_report` |
| `_compute_cost_usd` | — | `generate_report` |
| `_scan_prohibited_language` | — | `generate_report`; directly by `tests/test_llm_client.py`'s adversarial-fixture tests |
| `_drop_unknown_zones` | — | `generate_report` |
| `_call_with_retry` | `client.chat.completions.create` (the only real network call anywhere in this codebase), `asyncio.sleep` | `generate_report` |
| `generate_report` | `_build_messages`, `output_json_schema`, `_call_with_retry`, `LlmReportOutput.model_validate_json`, `_scan_prohibited_language`, `_drop_unknown_zones`, `_compute_cost_usd`; constructs `LlmMetadata` | whatever runs the pipeline for a patrol, right after `pipeline/payload.py::build_payload`; `tests/test_llm_client.py` (always with an injected mock `client`, never a real `AsyncOpenAI`) |

## What's not wired up yet

Nothing yet calls the A2–A5 chain (`segment_patrol` → `aggregate` →
`apply_image_selection` → `build_payload` → `llm.client.generate_report` →
`render_report` → `write_report`) automatically on `PATROL_END` + VIS
`_COMPLETE` — see this doc's intro. `VisWatcher.watch` is the other
still-unwired piece: it exists for A1's polling-until-complete behaviour,
but nothing calls it outside tests either. The fresh-patrol chain is
reachable today only from tests and from ad hoc scripts, such as the
manual smoke test (real `ai-report serve` process, real
`fake_rover`/`fake_vis` traffic, then a short script running the full
A1–A5 chain by hand — including a mocked `AsyncOpenAI` client, since no
test or smoke script in this repo ever makes a real LLM call — and
`write_report`'s `extra_writers`) used to verify A2–A5 end to end.

**`cli.py regenerate` is the exception** — it's real, wired, callable
production code today, verified as an actual subprocess (`python -m
ai_report.cli regenerate {patrol_id}`) against a report built by the
fresh-patrol chain, with `data/` deleted first to prove no rover/database
access happens. What A6 doesn't add: a way to build the *first* report for
a patrol automatically — `regenerate` only works once a `payload.json`
already exists from some earlier run (manual, in this repo, until
orchestration lands) and is meant for prompt-tuning iteration (spec §11),
not initial report generation.
