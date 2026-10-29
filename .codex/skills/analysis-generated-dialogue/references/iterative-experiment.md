# Iterative Dialogue-Pipeline Experiments

Use this reference when the task is not only to diagnose a completed batch, but to repeatedly test
pipeline hypotheses, compare branches, or decide whether a candidate should become a new version.

## Preserve the experimental question

Write the active problem and intended causal mechanism before changing anything. Keep low-frequency
cosmetic defects separate from repeated failures that affect the research claim. Do not switch the
experiment's objective merely because a new minor defect appears.

Separate three evaluation axes:

1. **Counselor:** context and work-state retention, strategy/mode choice, plan-to-utterance fidelity,
   grounding, therapeutic progress, and non-repetition.
2. **Client:** appraisal, behavior, process, memory, and numeric-state trajectories versus the actual
   public utterances.
3. **Interaction:** whether materially different client states and responses induce appropriate,
   meaningfully different counselor behavior and downstream progress.

## Trace handoffs before proposing a fix

For a material failure, inspect the shortest complete lineage:

```text
public dialogue and persistent state
→ stage A's actual prompt payload
→ stage A's raw and parsed output
→ any code-side transformation or fallback
→ stage B's actual prompt payload
→ stage B's output
→ final public utterance and next client response
```

Determine whether the defect originated in observation, compression, state persistence, routing,
handoff, execution, stochastic selection, or validation. A prompt that labels an input
`authoritative` is only a soft constraint unless code or a typed transition makes it binding.

Prefer typed state, explicit ownership, deterministic transitions, and code-enforced schema
invariants when a later model repeatedly reinterprets an earlier decision. Do not hide semantic
problems with brittle substring rules or case-specific wording patches.

## Validation ladder

Use the smallest test that can falsify the hypothesis, then widen only after it passes:

1. **Prompt or component probe:** realistic production-shaped input at the failing stage.
2. **Checkpoint replay:** multiple repeats across every known failure type, not one favorable case.
3. **Fresh closed loop:** regenerate both agents from an uncontaminated checkpoint so downstream
   effects and new failures can appear.
4. **Additional-profile validation:** use profiles and situations not used to develop the candidate.
5. **Production checkpoint:** integrate only after reporting evidence and obtaining user approval
   when approval is required.

Replay improvement is local evidence, not a general result. Do not call a candidate fixed until it
survives fresh closed-loop and additional-profile validation proportional to the claim.

## Cumulative regression matrix

Maintain all material criteria across experiments, including criteria the current change does not
target. Mark a criterion `not triggered` rather than silently omitting it. At minimum, monitor:

- active goal, request, boundary, selected direction, and work-stage continuity;
- mode/strategy/method appropriateness and diversity;
- exact plan-to-final-utterance fidelity;
- concrete therapeutic progress versus reflection or questioning loops;
- unsupported facts, interpretations, recommendations, or domain advice;
- client utterance versus appraisal, behavior, process, memory, and state changes;
- profile and trajectory diversity;
- parse failures, fallbacks, retries, and completion coverage.

Report counts and denominators for every triggered failure. Distinguish fixed target cases,
development profiles, and held-out profiles.

## Efficiency and evidence handling

- Build a compact index first; do not repeatedly load whole large JSON logs.
- Read public dialogue, selected plans, final utterances, and state deltas for every evaluated case.
- Expand only the causal stage logs for failures or representative counterexamples.
- Preserve raw artifacts and unique run IDs; never mix conditions on resume.
- Do not use an automatic judge as the sole authority. Directly inspect the relevant outputs and
  disclose any generator–judge coupling.
- When presenting qualitative evidence, show enough consecutive turns to reveal the trajectory,
  and translate excerpts only when the user requests it or the working language requires it.

## Experiment record

For each branch, record:

- run ID, date, code/config/prompt condition, endpoint/model, profiles, repeats, and completion;
- hypothesis and the causal reason for trying it;
- data inspected and analysis method;
- outcome on every cumulative criterion;
- representative artifact paths and line locations where practical;
- decision: reject, revise, advance to the next validation tier, or request integration approval.

## Stopping and acceptance

Reject or redesign a candidate when it only changes wording, improves one checkpoint while causing a
material regression, or repeatedly fails the same causal invariant. Stop widening a low-promise
branch rather than accumulating more data.

A candidate is ready to propose for integration when the core failure improves reproducibly, fresh
closed-loop behavior remains coherent, additional profiles show no material regression, and remaining
defects are rare or low-impact relative to the research objective. Do not chase perfect dialogue or
rare cosmetic defects indefinitely.
