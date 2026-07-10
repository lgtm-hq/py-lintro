"""SPDX license identifier normalization and license category sets.

This module provides a curated, offline mapping from common raw license
strings (as found in package metadata across ecosystems) to canonical SPDX
identifiers, plus category sets used by the policy presets. It intentionally
avoids a network dependency so license checks are deterministic in CI.
"""

from __future__ import annotations

import re

# Canonical SPDX identifiers that are broadly considered "permissive".
PERMISSIVE_LICENSES: frozenset[str] = frozenset(
    {
        "0BSD",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "ISC",
        "MIT",
        "MIT-0",
        "PSF-2.0",
        "Python-2.0",
        "Unlicense",
        "Zlib",
    },
)

# Weak copyleft licenses (file/library scoped) — allowed by "copyleft-ok".
WEAK_COPYLEFT_LICENSES: frozenset[str] = frozenset(
    {
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "MPL-2.0",
        "EPL-2.0",
        "CDDL-1.0",
    },
)

# Strong copyleft licenses (project scoped) — denied by most presets.
STRONG_COPYLEFT_LICENSES: frozenset[str] = frozenset(
    {
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
    },
)

# Non-open-source / source-available licenses — denied by default.
RESTRICTED_LICENSES: frozenset[str] = frozenset(
    {
        "SSPL-1.0",
        "BUSL-1.1",
        "Elastic-2.0",
        "Commons-Clause",
    },
)

# Sentinel used when a package declares "no license".
NO_LICENSE_MARKERS: frozenset[str] = frozenset(
    {
        "unlicensed",
        "none",
        "proprietary",
        "nolicense",
    },
)

# Raw string (lower-cased, normalized) -> SPDX identifier.
# Covers the most common metadata spellings across PyPI and npm.
_ALIASES: dict[str, str] = {
    "mit": "MIT",
    "mit license": "MIT",
    "mit-0": "MIT-0",
    "expat": "MIT",
    "apache": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache-2.0 license": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "asl 2.0": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd-2": "BSD-2-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd-3": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "isc": "ISC",
    "isc license": "ISC",
    "0bsd": "0BSD",
    "zero-clause bsd": "0BSD",
    "zlib": "Zlib",
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "cc0 1.0 universal": "CC0-1.0",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "psf": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "python-2.0": "Python-2.0",
    "python 2.0": "Python-2.0",
    "mpl": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "epl-2.0": "EPL-2.0",
    "eclipse public license 2.0": "EPL-2.0",
    "cddl-1.0": "CDDL-1.0",
    "lgpl": "LGPL-3.0-or-later",
    "lgpl-2.1": "LGPL-2.1-only",
    "lgpl-2.1-only": "LGPL-2.1-only",
    "lgpl-2.1-or-later": "LGPL-2.1-or-later",
    "lgpl-3.0": "LGPL-3.0-only",
    "lgpl-3.0-only": "LGPL-3.0-only",
    "lgpl-3.0-or-later": "LGPL-3.0-or-later",
    "lgplv3": "LGPL-3.0-only",
    "gpl": "GPL-3.0-or-later",
    "gplv2": "GPL-2.0-only",
    "gpl-2.0": "GPL-2.0-only",
    "gpl-2.0-only": "GPL-2.0-only",
    "gpl-2.0+": "GPL-2.0-or-later",
    "gpl-2.0-or-later": "GPL-2.0-or-later",
    "gplv3": "GPL-3.0-only",
    "gpl-3.0": "GPL-3.0-only",
    "gpl-3.0-only": "GPL-3.0-only",
    "gpl-3.0+": "GPL-3.0-or-later",
    "gpl-3.0-or-later": "GPL-3.0-or-later",
    "gnu general public license v3": "GPL-3.0-only",
    "agpl": "AGPL-3.0-or-later",
    "agpl-3.0": "AGPL-3.0-only",
    "agpl-3.0-only": "AGPL-3.0-only",
    "agpl-3.0-or-later": "AGPL-3.0-or-later",
    "sspl-1.0": "SSPL-1.0",
    "busl-1.1": "BUSL-1.1",
    "elastic-2.0": "Elastic-2.0",
}

# Canonical SPDX ids we recognize directly (case-insensitive match).
_KNOWN_SPDX: frozenset[str] = (
    PERMISSIVE_LICENSES
    | WEAK_COPYLEFT_LICENSES
    | STRONG_COPYLEFT_LICENSES
    | RESTRICTED_LICENSES
)

_SPDX_BY_LOWER: dict[str, str] = {spdx.lower(): spdx for spdx in _KNOWN_SPDX}

# Splits an SPDX expression such as "MIT OR Apache-2.0" into operands.
_EXPRESSION_SPLIT = re.compile(r"\s+(?:or|and)\s+", re.IGNORECASE)
_HAS_AND = re.compile(r"\sand\s", re.IGNORECASE)
_HAS_OR = re.compile(r"\sor\s", re.IGNORECASE)

# Prefer these when collapsing AND expressions so denials are not dropped.
_RESTRICTIVE_FOR_AND: frozenset[str] = (
    STRONG_COPYLEFT_LICENSES | WEAK_COPYLEFT_LICENSES | RESTRICTED_LICENSES
)


def _clean(raw: str) -> str:
    """Lower-case and collapse whitespace/punctuation noise in a license string.

    Args:
        raw: Raw license string.

    Returns:
        str: Normalized comparison key.
    """
    value = raw.strip().strip("()").strip()
    value = re.sub(r"\s+", " ", value)
    return value.lower()


def normalize_to_spdx(license_string: str | None) -> str | None:
    """Normalize an arbitrary license string to a canonical SPDX identifier.

    Handles direct SPDX identifiers, common metadata aliases, and simple SPDX
    expressions. ``OR`` expressions select the first recognized operand.
    ``AND`` expressions resolve every operand and prefer a restrictive /
    denied-class license so a conjunction cannot collapse to only the
    permissive side (e.g. ``MIT AND GPL-3.0-only`` → ``GPL-3.0-only``).

    Args:
        license_string: Raw license string from package metadata, or None.

    Returns:
        str | None: The SPDX identifier if recognized, otherwise None.
    """
    if not license_string:
        return None

    cleaned = _clean(license_string)
    if not cleaned or cleaned in NO_LICENSE_MARKERS:
        return None

    # Direct SPDX id match.
    if cleaned in _SPDX_BY_LOWER:
        return _SPDX_BY_LOWER[cleaned]

    # Alias match.
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]

    operands = [op for op in _EXPRESSION_SPLIT.split(cleaned) if op]
    if len(operands) <= 1:
        return None

    resolved_ids: list[str] = []
    for operand in operands:
        resolved = normalize_to_spdx(operand)
        if resolved is not None:
            resolved_ids.append(resolved)

    if not resolved_ids:
        return None

    # AND: every license applies — surface a restrictive operand when present.
    if _HAS_AND.search(cleaned) and not _HAS_OR.search(cleaned):
        for spdx_id in resolved_ids:
            if spdx_id in _RESTRICTIVE_FOR_AND:
                return spdx_id
        return resolved_ids[0]

    # OR (or mixed): first recognized operand wins.
    return resolved_ids[0]
