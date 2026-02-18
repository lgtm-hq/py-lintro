"""AI-powered issue intelligence for Lintro.

This package provides optional AI capabilities for summarizing, fixing,
and prioritizing linting issues. Requires installation with AI extras:

    uv pip install 'lintro[ai]'

Features:
    - AI-powered summaries with pattern detection and priority actions
    - Fix generation for issues that native tools can't auto-fix
    - Interactive suggestion review mode
    - Triage guidance for suppressing vs fixing issues
"""

from lintro.ai.availability import is_ai_available, require_ai
from lintro.ai.config import AIConfig

__all__ = [
    "AIConfig",
    "is_ai_available",
    "require_ai",
]
