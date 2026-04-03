"""Mapping from canonical tool names to Homebrew formula names.

Only entries where the brew formula name differs from the canonical
tool name need to be listed here.
"""

from __future__ import annotations

BREW_FORMULA_NAMES: dict[str, str] = {
    "markdownlint": "markdownlint-cli2",
    "osv_scanner": "osv-scanner",
}
