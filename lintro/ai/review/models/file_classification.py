"""File domain classification result."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileClassification:
    """Domain tags assigned to a changed file.

    Attributes:
        path: Repository-relative file path.
        domains: Matching review domains for the file.
    """

    path: str
    domains: list[str]
