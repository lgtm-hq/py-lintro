"""Centralized prompt templates for AI operations.

All prompts used by the summary and fix services are defined here for
maintainability and consistency.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Summary prompts (single API call for high-level actionable insights)
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM = (
    "You are a senior software engineer reviewing a codebase's quality report. "
    "Provide concise, actionable insights — not just restated counts. "
    "Focus on patterns, root causes, and prioritized recommendations. "
    "Respond ONLY with the requested JSON format, no markdown fences."
)

SUMMARY_PROMPT_TEMPLATE = """\
A linting analysis found {total_issues} issues across {tool_count} tool(s).

Here is a digest of all issues grouped by tool and error code:
{issues_digest}

Analyze these results and provide a structured summary.

Respond in this exact JSON format:
{{
  "overview": "2-3 sentence high-level assessment of code quality. Be specific about what needs attention.",
  "key_patterns": [
    "Pattern description with scope (e.g., 'Missing type annotations in 8 utility functions in src/utils/')"
  ],
  "priority_actions": [
    "Most impactful action to take first (explain why)"
  ],
  "triage_suggestions": [
    "Code + context where suppression is appropriate (e.g., 'B101 in test files — assert is idiomatic, add # noqa: B101')"
  ],
  "estimated_effort": "Rough time estimate to address all issues (e.g., '20-30 minutes of focused cleanup')"
}}

Guidelines:
- Identify systemic patterns, not individual issues
- Priority actions should be ordered by impact (fixes that resolve the most issues first)
- Be specific about file areas or patterns, not generic advice
- If issues are mostly cosmetic/style, say so
- Limit to 3-5 key patterns and 3-5 priority actions
- For triage_suggestions, identify issues that are likely intentional or idiomatic in their context (e.g., asserts in test files, long lines in generated code, unused imports in __init__.py). Suggest the appropriate suppression mechanism for the tool (# noqa, // eslint-disable, #[allow(...)], etc.). Only include if there are clear candidates — omit the field if all issues genuinely need fixing
"""

POST_FIX_SUMMARY_PROMPT_TEMPLATE = """\
An auto-fix session completed with these results:
- {applied} fixes applied successfully
- {rejected} fixes rejected by the user
- {remaining} issues still remaining

Remaining issues digest:
{issues_digest}

Provide a brief summary of the fix session and actionable next steps.

Respond in this exact JSON format:
{{
  "overview": "1-2 sentence summary of what was accomplished and what remains",
  "key_patterns": [
    "Pattern description of remaining issues"
  ],
  "priority_actions": [
    "Next step for remaining issues"
  ],
  "estimated_effort": "Rough effort to address remaining issues"
}}
"""

FIX_SYSTEM = (
    "You are a senior software engineer fixing code quality issues. "
    "Provide minimal, targeted fixes that resolve the reported issue "
    "without changing unrelated code. "
    "Respond ONLY with the requested JSON format, no markdown fences."
)

FIX_PROMPT_TEMPLATE = """\
Tool: {tool_name}
Error code: {code}
File: {file}
Line: {line}
Issue: {message}

Here is the relevant section of the file (lines {context_start}-{context_end}):
```
{code_context}
```

Provide a fix for this issue. Only change what is necessary.

Respond in this exact JSON format:
{{
  "original_code": "the exact lines that need to change (copy from above)",
  "suggested_code": "the corrected version of those lines",
  "explanation": "Imperative fix description (e.g. 'Add docstring for X')",
  "confidence": "high|medium|low"
}}
"""
