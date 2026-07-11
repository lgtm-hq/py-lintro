"""License detection helpers.

The detector focuses on normalizing raw license strings (from package
metadata or manifest fields) into canonical SPDX identifiers. Text-analysis
based detection (e.g. matching full license bodies) is intentionally out of
scope for this offline, deterministic implementation.
"""

from __future__ import annotations

from pathlib import Path

from lintro.licenses.spdx import normalize_to_spdx


class LicenseDetector:
    """Detect and normalize license identifiers from available sources."""

    def normalize_to_spdx(self, license_string: str | None) -> str | None:
        """Normalize a raw license string to an SPDX identifier.

        Args:
            license_string: Raw license string, or None.

        Returns:
            str | None: SPDX identifier if recognized, otherwise None.
        """
        return normalize_to_spdx(license_string)

    def detect_from_file(self, license_path: Path) -> str | None:
        """Detect an SPDX identifier from a LICENSE file's first line.

        This is a lightweight heuristic: it reads the file header and attempts
        to normalize it. It does not perform full license-text matching.

        Args:
            license_path: Path to a license file.

        Returns:
            str | None: SPDX identifier if the header is recognized.
        """
        if not license_path.is_file():
            return None
        try:
            header = license_path.read_text(errors="ignore").strip().splitlines()
        except OSError:
            return None
        if not header:
            return None
        return normalize_to_spdx(header[0])
