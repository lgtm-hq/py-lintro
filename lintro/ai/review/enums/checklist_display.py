"""Checklist visibility modes for review output.

Compatibility re-export: the enum now lives in :mod:`lintro.enums` so that
``lintro.config.review_config`` can reference it without importing
``lintro.ai`` (issue #724).
"""

from __future__ import annotations

from lintro.enums.checklist_display import ChecklistDisplay

__all__ = ["ChecklistDisplay"]
