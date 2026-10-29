# Version and Batch Adaptation

## Resolve the requested version

Preserve the exact user identifier, including underscores. Search case-insensitively but report the canonical directory and metadata values found.

Check likely locations without assuming they exist:

- `data/sessions/<VERSION>`
- version-specific output tags beneath `data/sessions/`
- explicitly supplied session files or directories
- `real_test/sessions` only when the user asks for interactive sessions

Then locate causal artifacts for the same version, commonly beneath:

- `src/<VERSION>` and `src/ccd_pipeline/<VERSION>`
- `prompt/<VERSION>`
- `config/<VERSION>`
- `src/analysis/<VERSION>`
- `tests/<VERSION>`

Use `rg --files` and searches for actual JSON field names when layouts differ.

## Select a batch

Prefer an explicit batch or explicit file list. For `latest`, group session filenames by their run-start timestamp when available and choose the lexicographically latest timestamp group. Verify:

- expected profile coverage
- consistent max turns and completion status
- consistent version and evaluation mode
- no older, partial, resume, or interactive sessions mixed in

If no reliable batch key exists, stop automatic aggregation and report the ambiguity. Do not equate file completion time with batch start time without evidence.

## Adapt the schema

Start from `turns` and discover fields from records:

- categorical appraisal values inside an appraisal-like object
- behavior labels inside plan or controller output
- numeric state snapshots and transition deltas
- memory selection, commit, and controller snapshot fields
- validity, training eligibility, parse errors, evaluator outputs

Build metrics only for fields present in the selected batch. Report per-field coverage when records differ.

Before comparing versions, inspect each version's schema and config snapshots. Compare constructs only after confirming semantic equivalence.

## Trace causes

For every material observation, follow the shortest supported path:

```text
raw turns
→ generated counselor/client distribution
→ appraisal or controller outputs
→ transition and option-gate rules
→ prompt/config/model conditions
→ analysis and plotting transformations
```

Stop where evidence stops. Label remaining explanations as hypotheses.
