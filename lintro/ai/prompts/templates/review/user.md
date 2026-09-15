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

**Every file this PR changes (this chunk's own files are marked
`— **(this chunk)**`):**

<{boundary}>
{pr_changed_files}
</{boundary}>

Unmarked files above are part of this PR but are not in this chunk's diff. Any copy of
them you read from disk is the stale base-commit version, not this PR's state — never
treat it as evidence that such a file was not updated, not touched, or missing a change.

---

### Interaction paths (trace each explicitly)

{interaction_paths}

---

### Review checklist ({checklist_count} items to check the diff against)

Check the diff against every item. Do not answer the items: report a finding, with
file:line evidence, for each item where the diff has a defect, and nothing for items
that hold. A concern is NOT a finding unless it also identifies a defect.

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
