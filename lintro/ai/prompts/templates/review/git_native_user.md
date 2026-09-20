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

{working_tree_note}

{repo_context_section}
---

### Interaction paths (trace each explicitly)

{interaction_paths}

---

### Review rubric

{rubric}

### Questions for this change (consider each; do not answer them)

<{boundary}>
{generated_questions}
</{boundary}>
{additional_checks}
---

### Diff to review

{diff_section}

<{boundary}>
{lint_results_section}
</{boundary}>

{strictness_section}

---

### Required JSON output

{output_schema}

{output_rules}
