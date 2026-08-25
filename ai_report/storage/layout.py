"""⑦ Storage — spec §11. Atomic writes: WEB polls `reports/{patrol_id}/`
directly and must never observe a partially written directory (ICD §C3.1).

`write_report` always writes `report.md` and `metadata.json`; anything else
that belongs in the same report directory — `images/` (A4's
`pipeline/select_images.py::copy_and_resize_images`), `payload.json` (A4's
`pipeline/payload.py::write_payload`), eventually whatever A5 adds — goes
through the `extra_writers` parameter rather than being written to the
final path directly.

> [!FLAG] This parameter exists because of a bug a manual end-to-end smoke
> test found: calling `copy_and_resize_images(..., dest_dir=final_dir)`
> *before* `write_report(...)` writes real image files at the final path,
> then `write_report`'s atomic swap — which builds a *fresh* temp
> directory containing only `report.md`/`metadata.json` and renames it
> over the final path — silently discards that `images/` directory, since
> it was never part of the swap. `extra_writers` closes that gap by giving
> every file-writing step a hook into the *same* temp directory before the
> one atomic rename. `pipeline/select_images.py` and `pipeline/payload.py`
> no longer take a bare `dest_dir` for this reason — see their callers below.

Called by: whatever orchestrates a full report build — currently only
`tests/test_layout.py`; production orchestration (on `PATROL_END` + VIS
`_COMPLETE`) is a later phase's addition. Calls `ai_report.models` (to
serialise `PatrolAggregate`) only — deliberately takes an already-rendered
Markdown string rather than calling `render/markdown.py` itself, so
storage stays decoupled from rendering (spec §1's ⑥/⑦ are separate stages).
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ai_report.models import PatrolAggregate


def write_report(
    patrol_id: str,
    report_md: str,
    metadata: PatrolAggregate,
    report_root: Path,
    extra_writers: list[Callable[[Path], None]] | None = None,
) -> Path:
    """Atomically (re)write `{report_root}/{patrol_id}/` with `report.md` + `metadata.json`
    plus whatever `extra_writers` add.

    Three-step directory swap, since POSIX `os.replace` can't atomically
    overwrite a *non-empty* destination directory in one call:

    1. Build the complete new directory at `{report_root}/.tmp_{patrol_id}/`:
       write `report.md`/`metadata.json`, then call each function in
       `extra_writers` with the tmp directory's `Path` so it can add its
       own files (e.g. `lambda tmp: copy_and_resize_images(agg, seg,
       data_root, tmp, settings)`, `lambda tmp: write_payload(payload,
       tmp)`). Nothing under the final path is touched yet.
    2. If a report already exists at the final path (regeneration — A6's
       `cli.py regenerate`), atomically rename it out of the way to
       `{report_root}/.old_{patrol_id}/` rather than deleting it.
    3. Atomically rename the tmp directory into the final path. If this
       step fails, the old report (if any) is renamed back into place
       before the exception propagates — the error-handling matrix's
       "Disk full on write -> fail loudly; leave the previous report
       intact" (spec §12) holds even on a failed regeneration, not just a
       first write.

    `generated_at` (required by `c3-metadata.schema.json` but deliberately
    absent from `PatrolAggregate` — see that model's docstring) is stamped
    here, immediately before serialisation, since this is the one place in
    the pipeline where a wall-clock timestamp is actually appropriate.

    Returns the final directory path. Raises whatever the underlying
    `OSError` was on failure (disk full, permissions, etc.) — this function
    does not swallow write errors, per spec §12; if an `extra_writer`
    raises, the whole tmp directory is discarded the same as a base-file
    write failure, and the previous report (if any) is left untouched.
    """
    report_root = Path(report_root)
    report_root.mkdir(parents=True, exist_ok=True)

    final_dir = report_root / patrol_id
    tmp_dir = report_root / f".tmp_{patrol_id}"
    old_dir = report_root / f".old_{patrol_id}"

    # Leftovers from a previously interrupted write must not confuse this run.
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    if old_dir.exists():
        shutil.rmtree(old_dir)

    tmp_dir.mkdir()
    try:
        _write_files(tmp_dir, report_md, metadata)
        for write_extra in extra_writers or []:
            write_extra(tmp_dir)
    except Exception:
        # Not just OSError: an `extra_writer` is arbitrary caller code
        # (`copy_and_resize_images` runs PIL, `write_payload` runs Pydantic
        # serialisation) and can fail with anything. Catching only OSError
        # left `.tmp_{patrol_id}` behind on every other failure, contradicting
        # this function's own promise below. The previous report is still
        # untouched either way -- nothing has been renamed yet.
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    if final_dir.exists():
        os.replace(final_dir, old_dir)
    try:
        os.replace(tmp_dir, final_dir)
    except OSError:
        if old_dir.exists():
            os.replace(old_dir, final_dir)
        raise
    finally:
        if old_dir.exists():
            shutil.rmtree(old_dir, ignore_errors=True)

    return final_dir


def _write_files(tmp_dir: Path, report_md: str, metadata: PatrolAggregate) -> None:
    """Write `report.md` and `metadata.json` into an already-created `tmp_dir`.

    Called only by `write_report`, inside its try/except — kept as a
    separate function so the atomic-swap logic above isn't cluttered with
    per-file serialisation detail.
    """
    (tmp_dir / "report.md").write_text(report_md, encoding="utf-8")

    data = metadata.model_dump(mode="json")
    data["generated_at"] = datetime.now(UTC).isoformat()
    (tmp_dir / "metadata.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
