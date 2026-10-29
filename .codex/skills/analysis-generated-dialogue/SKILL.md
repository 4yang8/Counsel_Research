---
name: analysis-generated-dialogue
description: Analyze versioned batches or iterative experiments of generated counseling dialogue and diagnose trajectory, state, strategy, memory, handoff, and visualization problems. Use when the user asks to inspect sessions, compare versions or experiment branches, trace failures through prompts and code, or validate whether a proposed pipeline change survives replay and fresh dialogue.
---

# Analyze Generated Dialogue

Analyze generated dialogue as an evidence-first audit. Do not modify prompts, configs, source, logs, or plots unless the user separately requests a change.

## Required input

Require the user to name the version. Preserve identifiers such as `V3_1`; never silently substitute the newest version or assume V1. Ask for the version when it is missing.

Use the repository containing this skill as the default project root. Accept an explicit batch, session path, or output tag when supplied.

## Workflow

1. Resolve the requested version's session directory and source boundaries using [version-adaptation.md](references/version-adaptation.md).
2. Identify the intended batch before aggregating. Never silently mix previous runs, partial sessions, real-test sessions, or versions.
3. Choose the operating mode:
   - For a completed-batch diagnosis, follow the evidence-first workflow below.
   - For repeated hypothesis, replay, or pipeline-improvement experiments, also read and follow [iterative-experiment.md](references/iterative-experiment.md).
4. Run the bundled analyzer for a deterministic first pass when its schema and aggregation are relevant:

   ```bash
   python3 .codex/skills/analysis-generated-dialogue/scripts/analyze_sessions.py \
     --project-root . --version V3_1 --batch latest
   ```

   Add `--sessions PATH` when discovery is ambiguous. Add `--output PATH` only when the user requests a saved machine-readable report.
5. Read representative raw turns. Verify dominant and rare labels, negative turns, state changes, behavior choices, memory commits, parse errors, and evaluator modes against the source JSON.
6. Trace each material anomaly through the requested version's prompt, config, orchestration, transition, controller, evaluator, and analysis code. For modular pipelines, trace the exact payload and transformation at every relevant handoff rather than assuming the next stage received or obeyed the prior decision.
7. Audit visualization code separately. Check file selection, training/valid filters, session grouping, turn resets, aggregation denominators, and whether lines connect different sessions.
8. Report findings using the output contract below. Apply the evidence rules in [diagnostic-checklist.md](references/diagnostic-checklist.md).

## Version adaptation rules

- Treat the session JSON as the primary empirical artifact and the version's source as the causal implementation artifact.
- Infer appraisal dimensions and numeric state fields from observed records. Do not require V1 field names.
- Treat absent fields as schema coverage gaps, not as zero values.
- Confirm version-specific meanings before comparing versions. The same field name may have changed semantics.
- If the bundled script cannot adapt to a future schema, inspect several sessions, state the mismatch, and perform a targeted read-only analysis. Do not force misleading metrics.

## Analysis standards

- Separate observed facts, code-supported mechanisms, and hypotheses.
- Report denominators alongside percentages and counts.
- Use representative counterexamples, not only dominant examples.
- Distinguish generation-distribution problems from classifier, transition, option-gate, parameter, and plotting problems.
- Check whether one model generates and judges the same dialogue; describe this as a coupling risk unless evidence establishes causality.
- Do not interpret latent simulator values as clinical scores.
- Do not recommend random negative behavior merely to balance a chart. Tie proposed experimental variation to the research question.
- Separate counselor quality, client-simulator coherence, and their interaction effect. Do not treat improvement on one axis as evidence that the others improved.
- A valid structured output is not proof of semantic compliance. Check whether the final utterance actually implements the selected plan, respects evidence boundaries, and advances the active work.

## Output contract

Lead with a short diagnosis, then provide:

1. **Scope**: version, batch rule, files, profiles, turns, completion and evaluation modes.
2. **Observed distributions**: appraisal, behavior, state, memory, failures, and cross-profile variation.
3. **Representative evidence**: specific session/turn examples supporting and challenging the dominant pattern.
4. **Causal trace**: data → prompt/config → code mechanism → observed result.
5. **Visualization validity**: genuine data pattern versus plotting or filtering artifact.
6. **Prioritized improvements**: only when requested or clearly useful; keep implementation separate from diagnosis.
7. **Uncertainty**: schema gaps, ambiguous batch selection, missing prompts, or alternative explanations.

Link local evidence files when possible. Do not claim that an issue is fixed unless a separate change-and-test task was completed.

## Resources

- `scripts/analyze_sessions.py`: version-parameterized, dependency-free batch statistics and anomaly signals.
- `references/version-adaptation.md`: version and batch discovery, schema adaptation, and causal source lookup.
- `references/diagnostic-checklist.md`: evidence requirements and common mechanisms to verify.
