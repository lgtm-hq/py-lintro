"""Prompt templates for AI diff-based code review."""

from __future__ import annotations

from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem

__all__ = [
    "REVIEW_ADVERSARIAL_SWEEP_TEMPLATE",
    "REVIEW_GENERATE_QUESTIONS_TEMPLATE",
    "REVIEW_OUTPUT_SCHEMA",
    "REVIEW_SYSTEM",
    "REVIEW_USER_PROMPT_TEMPLATE",
    "format_changed_files_for_prompt",
    "format_checklist_table_for_prompt",
    "format_deferred_scope_section",
    "format_external_review_section",
    "format_lint_results_section",
]

# Production prompt strings below are copied verbatim from the v3.1 spec.
# ruff: noqa: E501

REVIEW_SYSTEM = """\
You are a senior staff engineer performing a pre-merge code review. Your job is to find logic bugs, integration gaps, silent failure modes, and test contract weaknesses — the kinds of issues that pass linters and unit tests but fail in production.

You review code diffs across languages and domains: shell scripts, GitHub Actions, Python, Rust, TypeScript/JavaScript, API contracts, middleware, tests, and documentation. You trace execution paths mentally: follow conditionals, default values, HTTP status codes, exit codes, and cross-file wiring (workflow inputs → env vars → script behavior → server routes → middleware → DB → client parsing → UI).

**Review method (follow in order):**

1. Read the diff and changed-file list.
2. Trace every interaction path provided in the user prompt.
3. Cross-check OpenAPI/docs against new routes, presets, and error shapes when applicable.
4. Complete every checklist item — answer yes/no with file:line evidence.
5. Report every checklist **yes** as a finding (merge related items that share a root cause).
6. Scan for additional issues not covered by the checklist.
7. Output JSON only.

**Focus on:**

1. **LOGIC BUGS** — wrong precedence, inverted conditions, off-by-one, wrong variable
2. **SILENT FAILURES** — exit 0 / HTTP 200 when work was skipped or security checks should fail; fail-open when fail-closed is required
3. **DEFAULT INTERACTIONS** — new defaults breaking callers; feature A blocking feature B; grace-period vs expired vs active
4. **TIMESTAMP/DATA HANDLING** — empty strings, null coalescing order, sort key vs filter key mismatch (jq/shell/API fields)
5. **CI INTEGRATION** — egress policies, permissions, env wiring, URL encoding for API paths, action SHA pinning
6. **TEST GAPS** — incomplete migration tests; implicit setup() dependencies; visibility-only assertions; mock internals not behavior
7. **DOC/CONTRACT DRIFT** — documented behavior ≠ implementation; docs claiming hosts/presets the code does not provide
8. **DATA SEMANTICS** — jq coalescing (`//` vs `// empty`); empty timestamp comparisons; server prose errors vs client substring matching
9. **CONTROL-FLOW ORDER** — what happens BEFORE early returns; can independent work proceed when an optional step fails?
10. **SECURITY EXIT SEMANTICS** — trace security-sensitive branches to exit 0 vs exit 1 / HTTP 403 vs 200
11. **TEST DEFAULTS vs PRODUCTION DEFAULTS** — compare test setup/fixture defaults to workflow/script/production defaults
12. **BREAKING DEFAULT CHANGES** — intentional default changes without migration guidance or caller updates

**Do NOT report:**

- Style/formatting issues linters would catch
- Missing docstrings unless they hide a behavioral contract
- Deferred scope explicitly listed in the PR summary (if provided)
- Suggestions to refactor unrelated code
- Issues already fixed in later commits (review the final merged state)

**Severity (fixed scale — not configurable):**

- **P1:** Production bug, security bypass, or silent data loss — must fix before merge
- **P2:** Incorrect edge-case behavior, contract drift, or incomplete test coverage
- **P3:** Breaking default needing migration notes, UX wording, minor inaccuracy, or test isolation nit

Respond ONLY with valid JSON. No markdown fences."""

REVIEW_USER_PROMPT_TEMPLATE = """\
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

---

### Required JSON output

{output_schema}

**Rules:**

- Include all **{checklist_count}** checklist entries in `checklist` (even if answer is "no").
- Every checklist **yes** must have a corresponding finding (link via `checklist_ids`).
- Do not duplicate findings — merge related checklist items when they share a root cause.
- Prioritize cross-file integration bugs over isolated nits."""

REVIEW_OUTPUT_SCHEMA = """\
{
  "summary": "2-3 sentence assessment (safe to merge / merge with fixes / needs rework)",
  "checklist": [
    {
      "id": 1,
      "answer": "yes|no",
      "evidence": "One sentence — file:line or logic trace"
    }
  ],
  "findings": [
    {
      "severity": "P1|P2|P3",
      "category": "logic-bug|silent-failure|integration|test-gap|contract-drift|security|breaking-change",
      "file": "path/to/file",
      "line": 123,
      "title": "Short title (5-8 words)",
      "description": "What is wrong and why it matters in production",
      "cause": "Root cause — trace the code path",
      "fix": "Concise fix suggestion",
      "confidence": "high|medium|low",
      "checklist_ids": [1, 2]
    }
  ]
}"""

REVIEW_GENERATE_QUESTIONS_TEMPLATE = """\
You are generating domain-specific review questions for a code diff.

Read the diff and changed files. Generate 5-10 additional yes/no checklist questions tailored to THIS specific change.

Output JSON only:
{{"generated_questions": [{{"id": "G1", "question": "...", "rationale": "..."}}]}}

Diff:
{diff}
Changed files:
{changed_files}"""

REVIEW_ADVERSARIAL_SWEEP_TEMPLATE = """\
You previously reviewed this diff. Perform adversarial "what did I miss?" sweep.

Prior findings (do not duplicate):
{prior_findings_json}

Diff:
{diff}

Output JSON: {{"findings": [...]}} — NEW findings only. Empty array if nothing new."""


def format_checklist_table_for_prompt(*, items: list[ChecklistItem]) -> str:
    """Format checklist items as a numbered markdown table.

    Args:
        items: Selected checklist items sorted by id.

    Returns:
        Markdown table with prompt row numbers and questions.
    """
    lines = [
        "| # | Category | Question |",
        "|---|----------|----------|",
    ]
    for item in items:
        lines.append(
            f"| {item.id} | {item.category.value} | {item.question} |",
        )
    return "\n".join(lines)


def format_changed_files_for_prompt(*, files: list[ChangedFile]) -> str:
    """Format changed files as a bullet list with status.

    Args:
        files: Changed files from review context.

    Returns:
        Bullet list suitable for prompt injection.
    """
    if not files:
        return "- (no changed files)"
    return "\n".join(
        f"- `{file.path}` ({file.status}, +{file.additions}/-{file.deletions})"
        for file in files
    )


def format_deferred_scope_section(*, text: str | None) -> str:
    """Format optional deferred scope block for the review prompt.

    Args:
        text: Deferred scope description from PR summary, if any.

    Returns:
        Markdown block or empty string when no deferred scope.
    """
    if not text or not text.strip():
        return ""
    return f"**Deferred:** {text.strip()}"


def format_external_review_section(*, flags: list[str] | None) -> str:
    """Format optional external review tool flags section.

    Args:
        flags: External tool flags to verify against current code.

    Returns:
        Markdown block or empty string when no flags provided.
    """
    if not flags:
        return ""
    joined = ", ".join(flags)
    return f"**External tools flagged:** {joined} — verify against current code."


def format_lint_results_section(*, digest: str | None) -> str:
    """Format lint digest for prompt injection.

    Args:
        digest: Compact lint results digest, if any.

    Returns:
        XML-wrapped digest or empty string when no lint results.
    """
    if not digest or not digest.strip():
        return ""
    return f"<lint_results>\n{digest.strip()}\n</lint_results>"
