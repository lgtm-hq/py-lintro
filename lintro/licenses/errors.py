"""Errors raised when an ecosystem's license inventory cannot be collected."""

from pathlib import Path


class LicenseCollectionError(Exception):
    """An uncollected ecosystem, with its manifest and failure reason."""

    def __init__(self, ecosystem: str, source_file: Path, reason: str) -> None:
        """Record the collection failure for reporting.

        Args:
            ecosystem: Ecosystem whose inventory could not be collected.
            source_file: Manifest that could not be read or validated.
            reason: Explanation of the failure.
        """
        self.ecosystem = ecosystem
        self.source_file = source_file
        self.reason = reason
        super().__init__(f"{ecosystem} inventory uncollected ({source_file}): {reason}")
