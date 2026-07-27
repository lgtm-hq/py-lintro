"""File domain enumeration for review file classification.

Compatibility re-export: the enum now lives in :mod:`lintro.enums` so that
``lintro.config.review_config`` can reference it without importing
``lintro.ai`` (issue #724).
"""

from __future__ import annotations

from lintro.enums.file_domain import FileDomain

__all__ = ["FileDomain"]
