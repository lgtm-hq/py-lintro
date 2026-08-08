"""Cold-start import budget guards for issue #1305."""

from __future__ import annotations

import re
import subprocess  # nosec B404 - subprocess used only with fixed sys.executable -c argv in tests
import sys

from assertpy import assert_that

# Modules that must stay out of ``import lintro.cli`` / --version cold path.
_FORBIDDEN_AFTER_CLI_IMPORT = (
    "lintro.ai.review",
    "lintro.ai.review.checklist",
    "lintro.utils.tool_executor",
    "lintro.plugins.base",
    "lintro.tools.definitions.ruff",
    "lintro.cli_utils.commands.check",
    "rich",
    "pydantic",
    "loguru",
)


def test_import_lintro_cli_avoids_heavy_modules() -> None:
    """``import lintro.cli`` must not pull AI/review/plugins/tool trees.

    Runs in a subprocess so the assertion is independent of whatever the
    pytest process has already imported.
    """
    forbidden_repr = ", ".join(repr(name) for name in _FORBIDDEN_AFTER_CLI_IMPORT)
    script = f"""
import sys
import lintro.cli  # noqa: F401
forbidden = [{forbidden_repr}]
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise SystemExit("unexpected modules after import lintro.cli: " + ", ".join(loaded))
"""
    result = subprocess.run(  # nosec B603 - fixed argv: sys.executable -c with literal script; shell=False
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_equal_to("")


def test_lintro_version_avoids_heavy_modules() -> None:
    """``lintro --version`` must not import check/AI/plugin trees or rich.

    Uses Click's programmatic invocation so the assertion stays in-process of
    the subprocess under test without requiring a PATH entry for ``lintro``.
    """
    forbidden_repr = ", ".join(repr(name) for name in _FORBIDDEN_AFTER_CLI_IMPORT)
    script = f"""
import sys
from lintro.cli import cli
cli.main(args=["--version"], standalone_mode=False)
forbidden = [{forbidden_repr}]
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise SystemExit(
        "unexpected modules after lintro --version: " + ", ".join(loaded)
    )
"""
    result = subprocess.run(  # nosec B603 - fixed argv: sys.executable -c with literal script; shell=False
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_equal_to("")
    assert_that(bool(re.search(r"\d+\.\d+\.\d+", result.stdout))).is_true()


def test_import_config_loader_avoids_ai_review() -> None:
    """``import lintro.config.config_loader`` must not import AI review.

    pydantic may still load via other config models; AI review must not.
    """
    script = """
import sys
import lintro.config.config_loader  # noqa: F401
forbidden = [
    "lintro.ai.review",
    "lintro.ai.review.checklist",
    "lintro.ai.config",
]
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise SystemExit(
        "unexpected modules after import config_loader: " + ", ".join(loaded)
    )
"""
    result = subprocess.run(  # nosec B603 - fixed argv: sys.executable -c with literal script; shell=False
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_equal_to("")
