#!/usr/bin/env python3
"""Validate every schema in contracts/schemas/ and every fixture that claims
to match one. Run before any commit touching a schema or fixture, per
CLAUDE.md's working agreement.

Usage:
    pip install jsonschema
    python contracts/validate.py

Fixture-to-schema mapping is by filename convention, not configuration:
  telemetry.jsonl   -> c1-telemetry.schema.json  (one JSON object per line)
  events.jsonl      -> c1-event.schema.json      (one JSON object per line)
  analysis/*.json   -> c2-analysis.schema.json   (one JSON object per file)
  metadata.json     -> c3-metadata.schema.json   (one JSON object per file)

Exit code is 0 only if every schema is itself a valid JSON Schema and every
matched fixture file validates against its schema. A fixture directory with
no recognised files is not an error (nothing to check yet) but is reported,
since a golden fixture with only prose and no data can't actually be used
as a test input — see contracts/fixtures/patrol_20260813_1430/README.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

CONTRACTS_ROOT = Path(__file__).resolve().parent
SCHEMAS_DIR = CONTRACTS_ROOT / "schemas"
FIXTURES_DIR = CONTRACTS_ROOT / "fixtures"

_SCHEMA_BY_FILENAME = {
    "telemetry.jsonl": "c1-telemetry.schema.json",
    "events.jsonl": "c1-event.schema.json",
    "metadata.json": "c3-metadata.schema.json",
}


def load_schemas() -> dict[str, dict]:
    """Load every *.schema.json in SCHEMAS_DIR and confirm each is a valid
    JSON Schema (structurally — not that any data validates against it yet).

    Called once by `main`. Returns {filename: parsed schema}.
    """
    schemas: dict[str, dict] = {}
    errors: list[str] = []
    for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: not valid JSON ({exc})")
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            errors.append(f"{path.name}: not a valid JSON Schema ({exc.message})")
            continue
        schemas[path.name] = schema

    if errors:
        for err in errors:
            print(f"SCHEMA ERROR: {err}", file=sys.stderr)
        raise SystemExit(1)
    return schemas


def _validate_instance(instance: object, schema: dict, schema_name: str, where: str) -> str | None:
    """Validate one JSON instance against `schema`. Returns an error string, or None on success."""
    validator = Draft202012Validator(schema)
    errs = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    if not errs:
        return None
    first = errs[0]
    loc = "/".join(str(p) for p in first.path) or "<root>"
    return f"{where} failed {schema_name} at {loc}: {first.message}"


def validate_fixtures(schemas: dict[str, dict]) -> list[str]:
    """Walk every patrol fixture directory and validate recognised files.

    Called once by `main`. Returns a list of human-readable error strings
    (empty if every recognised fixture file validated cleanly).
    """
    errors: list[str] = []
    fixture_dirs = sorted(p for p in FIXTURES_DIR.iterdir() if p.is_dir())

    if not fixture_dirs:
        print(f"NOTE: no fixture directories found under {FIXTURES_DIR}")
        return errors

    for fixture_dir in fixture_dirs:
        found_any = False

        for filename, schema_name in _SCHEMA_BY_FILENAME.items():
            path = fixture_dir / filename
            if not path.exists():
                continue
            found_any = True
            schema = schemas.get(schema_name)
            if schema is None:
                errors.append(f"{path}: no {schema_name} loaded to validate against")
                continue

            if filename.endswith(".jsonl"):
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if not line.strip():
                        continue
                    instance = json.loads(line)
                    err = _validate_instance(instance, schema, schema_name, f"{path}:{lineno}")
                    if err:
                        errors.append(err)
            else:
                instance = json.loads(path.read_text(encoding="utf-8"))
                err = _validate_instance(instance, schema, schema_name, str(path))
                if err:
                    errors.append(err)

        analysis_dir = fixture_dir / "analysis"
        if analysis_dir.is_dir():
            schema = schemas.get("c2-analysis.schema.json")
            for json_path in sorted(analysis_dir.glob("*.json")):
                found_any = True
                instance = json.loads(json_path.read_text(encoding="utf-8"))
                err = _validate_instance(instance, schema, "c2-analysis.schema.json", str(json_path))
                if err:
                    errors.append(err)

        if not found_any:
            print(f"NOTE: {fixture_dir} has no recognised fixture data files yet (README only)")

    return errors


def main() -> int:
    schemas = load_schemas()
    print(f"Loaded {len(schemas)} schema(s) from {SCHEMAS_DIR}: {', '.join(sorted(schemas))}")

    errors = validate_fixtures(schemas)
    if errors:
        for err in errors:
            print(f"FIXTURE ERROR: {err}", file=sys.stderr)
        print(f"\n{len(errors)} fixture validation error(s).", file=sys.stderr)
        return 1

    print("All schemas valid; all present fixtures validate against their schema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
