"""A6 acceptance criterion: "Editing the prompt without bumping
PROMPT_VERSION fails a test."

If this test fails, it means `llm/prompts.py::SYSTEM_PROMPT` was edited.
That's fine — but it means a deliberate step was skipped: bump
`config.PROMPT_VERSION`, then update `SYSTEM_PROMPT_SHA256` in
`llm/prompts.py` to the hash this test computes (printed in the failure
message below). Both together are what keeps `metadata.json`'s
`llm.prompt_version` and `payload.json`'s `Payload.prompt_version`
trustworthy as a comparison key across prompt revisions.
"""

from __future__ import annotations

import hashlib

from ai_report.llm.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_SHA256


def test_system_prompt_matches_pinned_hash():
    actual = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert actual == SYSTEM_PROMPT_SHA256, (
        f"SYSTEM_PROMPT changed but SYSTEM_PROMPT_SHA256 wasn't updated to match.\n"
        f"Bump config.PROMPT_VERSION, then set:\n"
        f'    SYSTEM_PROMPT_SHA256 = "{actual}"\n'
        f"in llm/prompts.py."
    )
