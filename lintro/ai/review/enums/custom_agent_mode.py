"""Custom review agent activation modes.

Compatibility re-export: the enum lives in :mod:`lintro.enums` so that
``lintro.config.review_config`` can reference it without importing
``lintro.ai`` (issue #724).
"""

from __future__ import annotations

from lintro.enums.custom_agent_mode import CustomAgentMode

__all__ = ["CustomAgentMode"]
