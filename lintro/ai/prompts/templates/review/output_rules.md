**Rules:**

- Include all **{checklist_count}** checklist entries in `checklist` (even if answer is
  "no").
- Every checklist **yes** must have a corresponding finding (link via `checklist_ids`).
- Do not duplicate findings — merge related checklist items when they share a root
  cause.
- Prioritize cross-file integration bugs over isolated nits.
- `summary.headline` is exactly one sentence stating what the change does — not an
  assessment of whether it should merge.
- `summary.walkthrough` holds 3–6 bullets, each one sentence, covering the change in
  the order a reviewer would read it. When a bullet describes code you also reported a
  finding for, set that bullet's `finding_ref` to the finding's `file:line`; otherwise
  use an empty string.
- **Do not score or state a verdict.** The merge-readiness verdict is computed by
  lintro from the severities of the open findings (any P1 → Blocked; else any P2 →
  Changes requested; else any P3 → Nits only; else Ready). Write only the reasoning:
  `verdict_reasoning.deciding_factor` names the single issue that decides it (or says
  plainly that nothing blocks the merge) and `verdict_reasoning.failure_mechanism`
  traces how that issue fails in production. Two short paragraphs at most, total.
- `verdict_reasoning.files_needing_attention` lists the paths a reviewer should open
  first; leave it empty when nothing needs attention.
- `file_assessments` holds one entry per reviewed file with a single-sentence
  `overview`. Do not include severity counts — lintro derives those from `findings`.
- Every finding `title` must be a single line with no line breaks.
