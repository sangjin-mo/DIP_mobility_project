# Contracts

**The schemas here are the source of truth for every message crossing a
subsystem boundary.** The prose in `docs/01-interface-contracts.md` is
commentary on these files; where the two disagree, the schema wins.

## Layout

```
contracts/
├── schemas/      JSON Schema (2020-12) — one file per message type
├── fixtures/     Golden test data, shared by all four teams
└── validate.py   CI check: every fixture must validate against its schema
```

## How this prevents integration failure

Four teams hand-writing code from prose tables drift apart silently and
discover it at integration. Instead:

- **Producers** assert their output validates against the schema.
- **Consumers** assert their parser accepts the golden fixtures.
- **CI** validates the fixtures on every PR.

Neither side can break the other without a red build.

## Running locally

```bash
pip install jsonschema
python contracts/validate.py
```

## Generating models

Do not hand-write Pydantic models for boundary messages:

```bash
pip install datamodel-code-generator
datamodel-codegen \
  --input contracts/schemas/c1-telemetry.schema.json \
  --input-file-type jsonschema \
  --output ai_report/models/telemetry.py \
  --output-model-type pydantic_v2.BaseModel
```

## Changing a schema

Interfaces freeze at the end of Phase A0. After that:

1. Open a PR touching `contracts/schemas/`.
2. Update the affected fixtures in the same PR.
3. Get approval from both the producing and consuming team.
4. CI must pass before merge.

Adding an optional field is backward-compatible. Removing a field, retyping
one, or narrowing an enum is not — those need a coordinated release.

## Fixture design

Fixtures encode edge cases, not happy paths. See
`fixtures/patrol_20260813_1430/README.md` for what each case exercises and the
hand-computed expected aggregate that any implementation must reproduce.
