"""SPDX license identifier normalization and license category sets.

Canonical SPDX identifiers come from build-time codegen
(``lintro.licenses._spdx_data``). Compound expressions and known SPDX keys are
parsed via ``license-expression``. A residual alias table covers metadata
spellings that ScanCode's SPDX key set does not resolve. Category frozensets
remain hand-curated policy judgment (SPDX has no copyleft-strength field).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, cast

from license_expression import (
    ExpressionError,
    ExpressionParseError,
    get_spdx_licensing,
)

if TYPE_CHECKING:
    from license_expression import Licensing

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

# Residual aliases for spellings ``license-expression`` does not resolve to the
# SPDX id we want. Exact SPDX keys that the library already handles are omitted.
_ALIASES: dict[str, str] = {
    "mit license": "MIT",
    "expat": "MIT",
    "apache": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache-2.0 license": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "asl 2.0": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd-3": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "isc license": "ISC",
    "zero-clause bsd": "0BSD",
    "cc0": "CC0-1.0",
    "cc0 1.0 universal": "CC0-1.0",
    "the unlicense": "Unlicense",
    "psf": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "python 2.0": "Python-2.0",
    "mpl": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "eclipse public license 2.0": "EPL-2.0",
    "lgpl": "LGPL-3.0-or-later",
    "lgplv3": "LGPL-3.0-only",
    # license-expression maps bare "gpl" to GPL-1.0-or-later; keep our policy.
    "gpl": "GPL-3.0-or-later",
    "gplv2": "GPL-2.0-only",
    "gplv3": "GPL-3.0-only",
    "gnu general public license v3": "GPL-3.0-only",
    "agpl": "AGPL-3.0-or-later",
}

_SPDX_DATA_ERROR = (
    "SPDX license data is missing. Run "
    "`python3 scripts/release/generate_spdx_data.py` to generate "
    "`lintro/licenses/_spdx_data.py`."
)


def _load_spdx_ids() -> frozenset[str]:
    """Load the generated SPDX identifier set.

    Returns:
        frozenset[str]: Canonical SPDX license identifiers.

    Raises:
        RuntimeError: If the generated data module is missing or empty.
    """
    try:
        from lintro.licenses._spdx_data import SPDX_LICENSE_IDS
    except ImportError as exc:
        raise RuntimeError(_SPDX_DATA_ERROR) from exc
    if not SPDX_LICENSE_IDS:
        raise RuntimeError(_SPDX_DATA_ERROR)
    return SPDX_LICENSE_IDS


@lru_cache(maxsize=1)
def _spdx_ids() -> frozenset[str]:
    """Cached accessor for generated SPDX IDs.

    Returns:
        frozenset[str]: Canonical SPDX license identifiers.
    """
    return _load_spdx_ids()


@lru_cache(maxsize=1)
def _spdx_by_lower() -> dict[str, str]:
    """Cached lowercased SPDX id lookup.

    Returns:
        dict[str, str]: Lowercased id -> canonical id.
    """
    return {spdx_id.lower(): spdx_id for spdx_id in _spdx_ids()}


@lru_cache(maxsize=1)
def _licensing() -> Licensing:
    """Cached ``license-expression`` SPDX licensing helper.

    Returns:
        Licensing: Parser/validator loaded with SPDX keys.
    """
    return get_spdx_licensing()


def _clean(raw: str) -> str:
    """Lower-case and collapse whitespace/punctuation noise in a license string.

    Only strips *balanced* outer parentheses so names that embed a parenthetical
    (e.g. ``Mozilla Public License 2.0 (MPL 2.0)``) keep their inner markers.

    Args:
        raw: Raw license string.

    Returns:
        str: Normalized comparison key.
    """
    value = raw.strip()
    while len(value) >= 2 and value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    value = re.sub(r"\s+", " ", value)
    return value.lower()


def _validate_expression_keys(expression: object) -> bool:
    """Return True when every non-exception license key is in generated SPDX data.

    Args:
        expression: Parsed ``license-expression`` tree.

    Returns:
        bool: Whether all license symbols are known SPDX IDs.
    """
    known = _spdx_ids()
    licensing = _licensing()
    for key in licensing.license_keys(expression, unique=True):
        # Exceptions (WITH right-hand side) are not in licenses.json.
        symbol = licensing.known_symbols.get(key)
        if symbol is not None and getattr(symbol, "is_exception", False):
            continue
        if key not in known:
            return False
    return True


def _try_parse_expression(raw: str) -> str | None:
    """Parse ``raw`` with license-expression and return a rendered SPDX string.

    Args:
        raw: License string to parse.

    Returns:
        str | None: Normalized SPDX expression/id, or None if unrecognized.
    """
    licensing = _licensing()
    try:
        expression = licensing.parse(raw, validate=True, strict=True)
    except (ExpressionError, ExpressionParseError):
        return None
    if expression is None:
        return None
    if not _validate_expression_keys(expression):
        return None
    return str(expression)


def normalize_to_spdx(license_string: str | None) -> str | None:
    """Normalize an arbitrary license string to a canonical SPDX id or expression.

    Handles direct SPDX identifiers (via generated data), residual metadata
    aliases, and compound SPDX expressions (``OR`` / ``AND`` / ``WITH`` /
    parentheses) via ``license-expression``. Expressions are preserved in
    normalized form so the policy engine can evaluate them per-branch.

    Args:
        license_string: Raw license string from package metadata, or None.

    Returns:
        str | None: SPDX identifier or expression if recognized, else None.
    """
    if not license_string:
        return None

    # Ensure generated data is loadable before any normalization work.
    _ = _spdx_ids()

    cleaned = _clean(license_string)
    if not cleaned or cleaned in NO_LICENSE_MARKERS:
        return None

    # Residual aliases first so policy-specific mappings (e.g. bare "gpl") win
    # over license-expression's default key choice.
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]

    # Parse via license-expression (covers expressions and deprecated-ID remaps
    # such as GPL-3.0 → GPL-3.0-only) before falling back to the generated set.
    parsed = _try_parse_expression(license_string.strip())
    if parsed is not None:
        return parsed

    if cleaned != license_string.strip().lower():
        parsed = _try_parse_expression(cleaned)
        if parsed is not None:
            return parsed

    # Direct case-insensitive SPDX id match against generated data.
    by_lower = _spdx_by_lower()
    if cleaned in by_lower:
        return by_lower[cleaned]

    return None


def parse_license_expression(license_id: str) -> object | None:
    """Parse a normalized SPDX id/expression into a ``license-expression`` tree.

    Args:
        license_id: Normalized SPDX identifier or expression.

    Returns:
        object | None: Parsed expression tree, or None if parsing fails.
    """
    licensing = _licensing()
    try:
        parsed = licensing.parse(license_id, validate=True, strict=True)
    except (ExpressionError, ExpressionParseError):
        return None
    return cast(object | None, parsed)
