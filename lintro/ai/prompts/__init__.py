"""Centralized prompt templates for AI operations.

All prompts used by the summary and fix services are defined here for
maintainability and consistency.
"""

from lintro.ai.prompts.fix import (
    FIX_BATCH_PROMPT_TEMPLATE,
    FIX_PROMPT_TEMPLATE,
    FIX_SYSTEM,
    REFINEMENT_PROMPT_TEMPLATE,
)
from lintro.ai.prompts.post_fix import POST_FIX_SUMMARY_PROMPT_TEMPLATE
from lintro.ai.prompts.review import (
    REVIEW_ADVERSARIAL_SWEEP_TEMPLATE,
    REVIEW_GENERATE_QUESTIONS_TEMPLATE,
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_SYSTEM,
    REVIEW_USER_PROMPT_TEMPLATE,
    format_changed_files_for_prompt,
    format_checklist_table_for_prompt,
    format_deferred_scope_section,
    format_external_review_section,
    format_lint_results_section,
)
from lintro.ai.prompts.summary import SUMMARY_PROMPT_TEMPLATE, SUMMARY_SYSTEM

__all__ = [
    "FIX_BATCH_PROMPT_TEMPLATE",
    "FIX_PROMPT_TEMPLATE",
    "FIX_SYSTEM",
    "POST_FIX_SUMMARY_PROMPT_TEMPLATE",
    "REFINEMENT_PROMPT_TEMPLATE",
    "REVIEW_ADVERSARIAL_SWEEP_TEMPLATE",
    "REVIEW_GENERATE_QUESTIONS_TEMPLATE",
    "REVIEW_OUTPUT_SCHEMA",
    "REVIEW_SYSTEM",
    "REVIEW_USER_PROMPT_TEMPLATE",
    "SUMMARY_PROMPT_TEMPLATE",
    "SUMMARY_SYSTEM",
    "format_changed_files_for_prompt",
    "format_checklist_table_for_prompt",
    "format_deferred_scope_section",
    "format_external_review_section",
    "format_lint_results_section",
]
