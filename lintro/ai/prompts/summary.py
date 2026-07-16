"""Prompt templates for AI summary generation.

Prompt bodies are loaded verbatim from packaged template files under
``lintro/ai/prompts/templates/summary``.
"""

from __future__ import annotations

from lintro.ai.prompts._loader import load_prompt_template

SUMMARY_SYSTEM = load_prompt_template("summary", "system.md")

SUMMARY_PROMPT_TEMPLATE = load_prompt_template("summary", "prompt.md")
