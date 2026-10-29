# Generated Dialogue Diagnostic Checklist

## Scope integrity

- Confirm requested version, batch, profiles, turn counts, status, resumes, and evaluation modes.
- Count all available files separately from selected files.
- Do not mix automated and interactive dialogue without an explicit comparison request.

## Quantitative checks

- Appraisal: counts and rates by dimension, dominant combinations, rare and negative turns, parse errors.
- Behavior: relationship process, content act, expression, allowed options versus selected options.
- State: initial/final range, first saturation turn, positive/negative/zero deltas, clipping, decay, and trajectory diversity.
- Memory: selected cards, allowed units, commits, known context, revealed trajectory, evaluation-mode semantics.
- Profiles: stable parameters, initial states, trajectory uniqueness, and whether profile content affects dynamics or only language.
- Failures: invalid turns, retries, fallbacks, training eligibility, evaluator parse errors.

## Qualitative checks

- Read representative dominant, rare, and counterexample turns.
- Check factual misunderstandings, over-validation, repetitive counselor structure, client vagueness, rupture and repair, and disclosure progression.
- Verify that rationales match actual selected labels and available options.

## Mechanisms to verify, not assume

- A counselor prompt directly optimizing the appraisal dimensions.
- The same model generating and evaluating dialogue.
- Fixed additive updates with clipping and no decay or diminishing return.
- Several positive appraisal edges overwhelming one negative edge.
- Rupture behaviors unavailable unless a current negative label opens the gate.
- Conservative or engaged fallback labels dominating parse failures.
- Identical initial states and stable parameters suppressing profile diversity.
- Evaluation-off memory semantics diverging from observed speech.
- Plotters mixing batches, excluding non-training turns, or connecting different sessions on one line.

## Evidence language

Use these categories explicitly:

- **Observed**: directly counted or quoted from selected artifacts.
- **Mechanism confirmed in code**: exact rule found in the requested version.
- **Inference**: plausible explanation supported by observations and implementation.
- **Unknown**: missing artifact, ambiguous schema, or untested alternative.

Do not call a distribution defective solely because it is imbalanced. Explain whether it conflicts with the intended experimental coverage.
