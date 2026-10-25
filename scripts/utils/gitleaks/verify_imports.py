"""Reject Gitleaks builds that include the unmaintained OpenPGP packages."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def validate_imports(imports: Sequence[str]) -> None:
    """Validate the actual dependency list from the Gitleaks build configuration.

    Args:
        imports: Package paths emitted by ``go list -deps``.

    Raises:
        ValueError: The closure is incomplete or contains affected OpenPGP code.
    """
    if "github.com/zricethezav/gitleaks/v8" not in imports:
        raise ValueError("missing Gitleaks command in dependency closure")
    affected = "golang.org/x/crypto/openpgp"
    if any(path == affected or path.startswith(f"{affected}/") for path in imports):
        raise ValueError("GO-2026-5932: Gitleaks must not include x/crypto/openpgp")


def main() -> int:
    """Check the build's dependency closure from standard input.

    Returns:
        Zero for an unaffected closure, one for missing or affected packages.
    """
    try:
        validate_imports(sys.stdin.read().splitlines())
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
