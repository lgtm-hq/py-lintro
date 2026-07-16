"""Prompt template for post-fix summary generation.

The prompt body is loaded verbatim from the packaged template file under
``lintro/ai/prompts/templates/post_fix``.
"""

from __future__ import annotations

from lintro.ai.prompts._loader import load_prompt_template

POST_FIX_SUMMARY_PROMPT_TEMPLATE = load_prompt_template(
    "post_fix",
    "summary_prompt.md",
)
