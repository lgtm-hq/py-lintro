"""Prompt templates for AI summary generation."""

from __future__ import annotations

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
  "overview": "2-3 sentence assessment of code quality. \
Be specific about what needs attention.",
  "key_patterns": [
    "Pattern description with scope \
(e.g., 'Missing type annotations in src/utils/')"
  ],
  "priority_actions": [
    "Most impactful action to take first (explain why)"
  ],
  "triage_suggestions": [
    "Code + context where suppression is appropriate \
(e.g., 'B101 in tests — add # noqa: B101')"
  ],
  "estimated_effort": "Rough time estimate \
(e.g., '20-30 minutes of focused cleanup')"
}}

Guidelines:
- Identify systemic patterns, not individual issues
- Priority actions should be ordered by impact (fixes that resolve the
  most issues first)
- Be specific about file areas or patterns, not generic advice
- If issues are mostly cosmetic/style, say so
- Limit to 3-5 key patterns and 3-5 priority actions
- When recommending actions, use `lintro chk` for checking and \
`lintro fmt` for formatting — never suggest running linting tools \
directly (e.g., don't say 'run black' or 'run ruff --fix')
- For triage_suggestions, identify issues that are likely \
intentional or idiomatic in their context (e.g., asserts in \
test files, long lines in generated code, unused imports in \
__init__.py). Suggest the appropriate suppression mechanism \
for the tool (# noqa, // eslint-disable, #[allow(...)], etc.). \
Use an empty array if all issues genuinely need fixing
"""
