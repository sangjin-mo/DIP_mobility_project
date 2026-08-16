# Architecture Decision Records

Numbered, dated, immutable once `Accepted`. A superseded ADR is never deleted or
edited in place — its status changes to `Superseded by ADR-NNNN` and the new
record links back. The reasoning is the artifact; losing it is how a team
re-litigates a settled question or silently reverts a decision that mattered.

Format is Nygard's: Context, Decision, Consequences.

Write one when a choice is expensive to reverse, non-obvious to a newcomer, or
was reversed at least once. Do not write one for routine implementation choices.

| ID | Title | Status |
|---|---|---|
| [0001](0001-three-tier-latency-split.md) | Split the system by latency tier | Accepted |
| [0002](0002-drop-donkeycar.md) | Do not use the Donkey Car framework | Superseded by 0007 |
| [0003](0003-event-based-zone-segmentation.md) | Segment zones by event, not elapsed time | Accepted |
| [0004](0004-llm-does-not-compute-numbers.md) | The LLM never computes numbers | Accepted |
| [0005](0005-structured-output-not-markdown.md) | Take JSON from the LLM, render Markdown ourselves | Accepted |
| [0006](0006-observation-count-not-plant-count.md) | Report observation counts, not plant counts | Accepted |
| [0007](0007-piracer-platform.md) | Platform is Waveshare PiRacer | Accepted |
| [0008](0008-schema-first-contracts.md) | Contracts are JSON Schema files, not prose | Accepted |
