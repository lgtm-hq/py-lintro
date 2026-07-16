"""Prompt templates for AI fix generation.

Prompt bodies are loaded verbatim from packaged template files under
``lintro/ai/prompts/templates/fix``; this module only re-exports the constants
so existing call sites stay stable.
"""

from __future__ import annotations

from lintro.ai.prompts._loader import load_prompt_template

FIX_SYSTEM = load_prompt_template("fix", "system.md")

FIX_PROMPT_TEMPLATE = load_prompt_template("fix", "prompt.md")

FIX_BATCH_PROMPT_TEMPLATE = load_prompt_template("fix", "batch_prompt.md")

REFINEMENT_PROMPT_TEMPLATE = load_prompt_template("fix", "refinement.md")
