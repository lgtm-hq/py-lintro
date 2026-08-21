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
  lintro from the severities of the open findings (any P1 → {label_blocked}; else any
  P2 → {label_changes_requested}; else any P3 → {label_nits_only}; else
  {label_ready}). Write only the reasoning:
  `verdict_reasoning.deciding_factor` names the single issue that decides it (or says
  plainly that nothing blocks the merge) and `verdict_reasoning.failure_mechanism`
  traces how that issue fails in production. Two short paragraphs at most, total.
- `verdict_reasoning.files_needing_attention` lists the paths a reviewer should open
  first; leave it empty when nothing needs attention.
- `file_assessments` holds one entry per reviewed file with a single-sentence
  `overview`. Do not include severity counts — lintro derives those from `findings`.
- Every finding `title` must be a single line with no line breaks.
- **P1 requires a concrete `failure_scenario`** — the inputs, the path taken, and the
  observable result. "Could be a problem" is not a failure mechanism. A P1 without one
  is automatically downgraded to P2 and the correction is recorded against the run, so
  an uncalibrated P1 buys you nothing but a logged downgrade.
- **Calibrate severity.** P1 means merge-blocking defect: expect 0–2 on a typical PR and
  none at all on most. When you are torn between P1 and P2, choose P2. When you are torn
  between P2 and P3, choose P3 — a single borderline P2 flips the derived verdict from
  nits_only to changes_requested. Assign P2 only when a caller, test, or documented
  contract is actually wrong. Name that rubric boundary in every finding `description`.
- Set `kind` to `question` when you suspect something but cannot show it — an
  assumption you want the author to confirm, context you lack. Questions carry no
  severity, never affect the verdict, and are capped at **3 per review**. Use them
  instead of inflating severity; if the answer confirms a defect you will report it as
  a normal finding next round.
- Set `evidence_style` honestly: `diff_local` when the diff hunk alone shows it,
  `cross_file` when you traced code outside the hunk, `speculative` when you inferred it
  without verifying. A speculative finding is not penalized — it is labelled, and its
  fix prompt tells the agent to reproduce it first.
- **Style and formatting a linter would catch are out of scope** — lintro runs the
  native linters in the same check run, so reporting them is pure noise. The one
  exception is correctness-adjacent style: shadowed names, misleading identifiers, and
  confusing API misuse stay in scope as P3 `code-smell`.
- Include `suggested_change` **only** when the fix is a clean hunk: `lines` is the
  inclusive `[start, end]` range it replaces (the finding's own `line` must fall inside
  it) and `replacement` is the *complete* new text for exactly those lines, indentation
  included. A partial replacement silently deletes the lines it omits. Omit the object
  entirely when the fix needs edits elsewhere, spans non-contiguous lines, or cannot be
  written out verbatim — a described fix is better than a wrong one-click commit. Keep
  `replacement` to at most 4,000 characters and the range to at most 200 lines; anything
  larger is dropped and rendered as a described fix anyway.
{findings_cap_rule}
