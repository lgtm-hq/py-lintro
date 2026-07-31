"""Checklist visibility modes for review output."""

from __future__ import annotations

from enum import auto

from lintro.enums.hyphenated_str_enum import HyphenatedStrEnum

__all__ = ["ChecklistDisplay"]


class ChecklistDisplay(HyphenatedStrEnum):
    """How structured checklist results appear in human-facing output.

    * **off** — findings only (default).
    * **linked** — review questions under findings with checklist_ids.
    * **all** — linked plus cleared-check and orphan appendices.
    """

    OFF = auto()
    LINKED = auto()
    ALL = auto()
