Review this code change for actionable findings.

**PR:** <{boundary}> {pr_title} </{boundary}>

**Base → Head:** <{boundary}> `{base_ref}`...`{head_ref}` </{boundary}>

**Summary:**

<{boundary}>
{pr_summary}
</{boundary}>

{deferred_scope_section}

<{boundary}>
{external_review_section}
</{boundary}>

**Changed files ({changed_file_count}):**

<{boundary}>
{changed_files}
</{boundary}>

---

### Interaction paths (trace each explicitly)

{interaction_paths}

---

### Mandatory checklist (complete all {checklist_count} before finalizing)

Answer every item. Any **yes** → add a finding. Any **no** → record in `checklist` with
brief evidence (file:line).

{checklist}

---

<pull_request_diff>
<{boundary}>
{diff}
</{boundary}>
</pull_request_diff>

<{boundary}>
{lint_results_section}
</{boundary}>

{strictness_section}

---

### Required JSON output

{output_schema}

{output_rules}
