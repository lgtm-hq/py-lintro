"""Prompt template for post-fix summary generation."""

from __future__ import annotations

POST_FIX_SUMMARY_PROMPT_TEMPLATE = """\
An auto-fix session completed with these results:
- {applied} fixes applied successfully
- {rejected} fixes rejected by the user
- {remaining} issues still remaining

Remaining issues digest:
{issues_digest}

Provide a brief summary of the fix session and actionable next steps.

Guidelines:
- When recommending actions, use `lintro chk` for checking and \
`lintro fmt` for formatting — never suggest running linting tools \
directly (e.g., don't say 'run black' or 'run ruff --fix')

Respond in this exact JSON format:
{{
  "overview": "1-2 sentence summary of what was accomplished \
and what remains",
  "key_patterns": [
    "Pattern description of remaining issues"
  ],
  "priority_actions": [
    "Next step for remaining issues"
  ],
  "estimated_effort": "Rough effort to address remaining issues"
}}
"""
