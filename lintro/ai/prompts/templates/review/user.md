Review this code change for actionable findings.

**PR:** {pr_title}

**Base → Head:** `{base_ref}`...`{head_ref}`

**Summary:**

{pr_summary}

{deferred_scope_section}

{external_review_section}

**Changed files ({changed_file_count}):**

{changed_files}

---

### Interaction paths (trace each explicitly)

{interaction_paths}

---

### Mandatory checklist (complete all {checklist_count} before finalizing)

Answer every item. Any **yes** → add a finding. Any **no** → record in `checklist` with brief evidence (file:line).

{checklist}

---

<pull_request_diff>
{diff}
</pull_request_diff>

{lint_results_section}

{strictness_section}

---

### Required JSON output

{output_schema}

**Rules:**

- Include all **{checklist_count}** checklist entries in `checklist` (even if answer is "no").
- Every checklist **yes** must have a corresponding finding (link via `checklist_ids`).
- Do not duplicate findings — merge related checklist items when they share a root cause.
- Prioritize cross-file integration bugs over isolated nits.