"""Tests for the buf installer path in ``install-tools.sh``."""

from __future__ import annotations

import subprocess
from pathlib import Path

from assertpy import assert_that

_INSTALL_TOOLS = Path("scripts/utils/install-tools.sh")


def test_buf_version_probe_survives_pipefail() -> None:
    """A broken or unversioned ``buf`` must not abort the installer.

    ``set -euo pipefail`` turns a failed ``buf --version`` pipeline into a
    hard exit. The probe has to yield an empty string so the download
    branch still runs.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    assert_that(script).contains("head -1 || true")

    result = subprocess.run(  # nosec B603 B607 - fixed bash argv in a controlled test
        [
            "/bin/bash",
            "-c",
            (
                "set -euo pipefail; "
                "buf() { return 1; }; "
                "installed_version=$("
                "buf --version 2>/dev/null | "
                "grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+' | "
                "head -1 || true); "
                'printf "version=%s\\n" "$installed_version"; '
                'printf "reached fallback\\n"'
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("version=")
    assert_that(result.stdout).contains("reached fallback")
    assert_that(result.stderr).is_empty()
