"""Centralized prompt templates for AI operations.

All prompts used by the summary and fix services are defined here for
maintainability and consistency.
"""

from lintro.ai.prompts.fix import FIX_PROMPT_TEMPLATE, FIX_SYSTEM
from lintro.ai.prompts.post_fix import POST_FIX_SUMMARY_PROMPT_TEMPLATE
from lintro.ai.prompts.summary import SUMMARY_PROMPT_TEMPLATE, SUMMARY_SYSTEM

__all__ = [
    "FIX_PROMPT_TEMPLATE",
    "FIX_SYSTEM",
    "POST_FIX_SUMMARY_PROMPT_TEMPLATE",
    "SUMMARY_PROMPT_TEMPLATE",
    "SUMMARY_SYSTEM",
]
