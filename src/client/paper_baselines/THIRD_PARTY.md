# Sources and attribution

## Korean translation

At the user's request, all 29 paper prompt templates, the four role instructions,
the runtime output contract, and PatientAct taxonomy descriptions were translated
into Korean. Examples were translated as well. Template variables, JSON keys,
enumerated label identifiers, retrieval tags and termination markers are preserved.
Natural-language output is now requested in Korean. The 40 original character
records remain unchanged. Stored session snapshots and historical logs are not
rewritten. These are translations/adaptations, not verbatim English paper prompts;
the source attributions and licenses below continue to apply.

## PatientAct / PatientHub

Sahand Sabour et al., *PatientAct: Theory-Grounded Mental Health Client Simulation*.
User-supplied PDF: `docs/비교논문/PatientAct - Theory-Grounded Mental Health Client Simulation.pdf`.

Code, taxonomy, prompts and 40 native characters were ported from
[Sahandfer/PatientHub](https://github.com/Sahandfer/PatientHub/tree/54d5c8761a9055a1e86d153715607ef9fa26358a),
commit `54d5c8761a9055a1e86d153715607ef9fa26358a`:

- `patienthub/clients/patientAct.py` → `patientact.py`.
- `patienthub/schemas/patientAct.py` → `data/patientact_constants.json`.
- `data/prompts/client/patientAct.yaml`, English block → `prompts/patientact_*.md`.
- `data/characters/patientAct.json` → `data/patientact.json`.

Changes: project HTTP/SSE transport, local JSON schema validation, atomic persistence,
blocked-content boolean instead of retaining blocked text inside prompt context, and bounded last-three behavior history.
Templates are rendered with Jinja2. No PatientHub runtime, Pydantic or OmegaConf dependency is required.
The unused `sys_prompt_no_memory` template is retained for source traceability; this version always uses gating and the full pipeline.

MIT License

Copyright (c) 2026 Sahand Sabour

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## CARS

Yihao Qin et al., *When Clients Stop Following: A Cognitive Conceptualization Diagram-driven
Framework for Strategic Counseling*. User-supplied PDF:
`docs/비교논문/When Clients Stop Following - A Cognitive Conceptualization Diagram-driven Framework for Strategic Counseling.pdf`.

`prompts/cars_profile.md` is the cognitive-pattern generation prompt (pp.13–14);
`prompts/cars_turn.md` is the cognitive reasoning/response prompt (pp.15–16).
Extracted from the supplied PDF with page markers/numbers removed and wrapped hyphenation joined.
The clinical theme placeholder is filled with the six themes listed in the prompt's output schema.
The CARS implementation is a local paper-based reimplementation, not an author-supplied CARS repository.
No permission or license beyond that of the original paper is asserted.

## CALM-IT

Viet Cuong Nguyen, Nhi Yen Nguyen, Kristin A. Candan, Mary Conlon, Vanessa Rumie,
Kristen Risola, Michael L. Birnbaum and Munmun De Choudhury,
*CALM-IT: Enhancing Synthetic Motivational Interviewing Dialogue Quality with Conversational Dynamics*.
User-supplied PDF:
`docs/비교논문/CALM-IT - Enhancing Synthetic Motivational Interviewing Dialogue Quality with Conversational Dynamics.pdf`.
[Paper](https://arxiv.org/abs/2601.10085), version used: v3.
The paper is distributed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
The extracted/adapted CALM-IT paper prompt files retain that attribution and license.

Figures A2–A16, pp.18–33 → corresponding `prompts/calmit_client_*.md` and
`prompts/calmit_counselor_*.md`. Figure A16 spans pp.32–33.
Changes: removal of page/figure labels, joined PDF hyphenation, named placeholders in place of
anonymous `{}`, and a runtime structured JSON output contract.
`calmit_summary.md`, `calmit_counselor_emotion.md`, `calmit_counselor_stagnation.md`
and `calmit_selector.md` are explicitly marked reconstructed/replacement instructions,
not supplied paper prompts. Original role-specific likelihood ranker weights are not included.

### CARS local validation extension (2026-11-05)

`cars_contract.md`, `cars_profile_review.md`, and `cars_response_review.md` are project-authored validation instructions, not translated paper prompts. The emotion wording in the profile/turn templates is clarified to consistently mean positive affect/engagement rather than anger intensity. Structured reasoning, source quotations, semantic review, and one bounded correction attempt are local additions; provenance identifies this execution as `cars-validated-v2`.
