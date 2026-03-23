"""Centralized prompt templates for AI operations.

All prompts used by the summary and fix services are defined here for
maintainability and consistency.
"""

from lintro.ai.prompts.summary import SUMMARY_PROMPT_TEMPLATE, SUMMARY_SYSTEM

__all__ = [
    "SUMMARY_PROMPT_TEMPLATE",
    "SUMMARY_SYSTEM",
]
