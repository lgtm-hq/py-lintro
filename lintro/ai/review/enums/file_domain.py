"""File domain enumeration for review file classification."""

from __future__ import annotations

from enum import auto

from lintro.enums.hyphenated_str_enum import HyphenatedStrEnum


class FileDomain(HyphenatedStrEnum):
    """Domain labels assigned to changed files during review."""

    SHELL = auto()
    CI = auto()
    PYTHON = auto()
    RUST = auto()
    TYPESCRIPT = auto()
    TEST = auto()
    DOCS = auto()
    API = auto()
    SECURITY = auto()
