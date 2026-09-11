"""Lockstep tests for the release verify step's couplings to lintro's UI.

``verify_built_binary.sh`` and ``drive_interactive_review.py`` classify a
release build by matching lintro's own human-readable copy: the missing-extra
``UsageError`` from the MCP command, the interactive review prompt, and its key
bindings. Those literals are hand-maintained in three other files, so without
these tests a reword inside ``lintro`` passes the whole suite and only surfaces
as a false packaging failure on the next tag (#2514).
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DRIVER_PATH = _REPO_ROOT / "scripts" / "build" / "drive_interactive_review.py"
_VERIFY_PATH = _REPO_ROOT / "scripts" / "build" / "verify_built_binary.sh"
_BATS_PATH = (
    _REPO_ROOT / "tests" / "bats" / "unit" / "build" / "test_verify_built_binary.bats"
)

# The literal the verify step greps for to accept a binary built without the
# optional `lintro[mcp]` extra, as written in the shell script itself.
_MCP_GREP_PATTERN = re.compile(r'grep -q "([^"]+)"')

# A pygments-rendered diff captured from a real pty session of the driver.
_HIGHLIGHTED_DIFF = (
    b"\x1b[91m--- a/bad.py\x1b[0m\r\n\r\n\x1b[92m+++ b/bad.py\x1b[0m\r\n\r\n"
    b"\x1b[1;95m@@ -1 +1 @@\x1b[0m\r\n\r\n\x1b[91m-import os\x1b[0m\r\n"
    b"\x1b[92m+# import removed\x1b[0m\r\n"
)

# The same diff after a degraded render: pygments never resolved a lexer, so
# the markers arrive bare.
_PLAIN_DIFF = (
    b"--- a/bad.py\r\n+++ b/bad.py\r\n@@ -1 +1 @@\r\n"
    b"-import os\r\n+# import removed\r\n"
)

# Coloured output that is not a diff: lintro's own table rules and summary
# counts put SGR sequences immediately before `+` and `-` mid-line.
_COLOURED_NON_DIFF = (
    b"  \x1b[92m3 auto-fixable issues\x1b[0m\r\n"
    b"| \x1b[91m-1\x1b[0m | \x1b[92m+2\x1b[0m |\r\n"
    b"\x1b[2m+------+------+\x1b[0m\r\n"
)


def _load_driver() -> ModuleType:
    """Import the pty driver without running it.

    Returns:
        The loaded ``drive_interactive_review`` module.
    """
    spec = importlib.util.spec_from_file_location(
        "drive_interactive_review",
        _DRIVER_PATH,
    )
    assert spec is not None and spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules["drive_interactive_review"] = module
    spec.loader.exec_module(module)
    return module


def _verify_mcp_marker() -> str:
    """Read the MCP acceptance literal out of the verify script.

    Returns:
        The unescaped text the shell script greps the MCP output for.
    """
    source = _VERIFY_PATH.read_text(encoding="utf-8")
    match = _MCP_GREP_PATTERN.search(source)
    assert_that(match).is_not_none()
    assert match is not None  # narrow type for mypy
    return match.group(1).replace("\\[", "[").replace("\\]", "]")


def test_mcp_marker_matches_the_usage_error_lintro_raises() -> None:
    """The verify step's MCP literal must come from ``require_mcp`` itself.

    A reword of the missing-extra ``UsageError`` would otherwise make the
    verify step treat a perfectly normal release binary as a packaging
    failure, discovered only on the next tag.
    """
    import click

    from lintro.mcp import require_mcp

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr("lintro.mcp.is_mcp_available", lambda: False)
        with pytest.raises(click.UsageError) as raised:
            require_mcp()

    assert_that(str(raised.value)).contains(_verify_mcp_marker())


def test_bats_stub_reuses_the_same_mcp_marker() -> None:
    """The bats stub must speak the copy the verify step accepts.

    The stub hard-codes the message a real binary prints; if it drifts from
    the grep literal the suite goes green while the release gate breaks.
    """
    assert_that(_BATS_PATH.read_text(encoding="utf-8")).contains(
        _verify_mcp_marker(),
    )


def test_prompt_marker_matches_the_interactive_review_prompt() -> None:
    """The driver waits for a prompt fragment lintro actually prints.

    Both prompt forms are checked: the fixture's behavioural-risk fixes
    produce the non-safe-default form today, but a safe-style default must
    not silently strand the driver either.
    """
    from lintro.ai.interactive import _render_prompt

    driver = _load_driver()
    marker = driver.PROMPT_MARKER.decode()

    for safe_default in (False, True):
        prompt = _render_prompt(validate_mode=False, safe_default=safe_default)
        assert_that(prompt).contains(marker)


def test_review_keys_match_the_review_key_bindings() -> None:
    """The keys the driver sends must be lintro's own diff and quit bindings."""
    from lintro.ai.interactive import ReviewKey

    driver = _load_driver()

    assert_that(driver.REVIEW_KEYS).is_equal_to(
        (ReviewKey.SHOW_DIFF.value.encode(), ReviewKey.QUIT.value.encode()),
    )


@pytest.mark.parametrize(
    ("captured", "expected"),
    [
        (_HIGHLIGHTED_DIFF, True),
        (_PLAIN_DIFF, False),
        (_COLOURED_NON_DIFF, False),
        (_PLAIN_DIFF + _COLOURED_NON_DIFF, False),
    ],
    ids=["highlighted", "plain", "coloured-non-diff", "degraded-run"],
)
def test_diff_was_highlighted_only_accepts_coloured_diff_lines(
    captured: bytes,
    expected: bool,
) -> None:
    """Only a diff whose own +/- lines carry colour may pass the gate.

    Args:
        captured: Bytes standing in for a pty session's output.
        expected: Whether the driver should call that a pygments render.
    """
    driver = _load_driver()

    assert_that(driver.diff_was_highlighted(captured)).is_equal_to(expected)


def test_build_environment_is_hermetic_and_fixture_first() -> None:
    """The child must see the fixtures first and no inherited colour policy."""
    driver = _load_driver()

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setenv("NO_COLOR", "1")
        patcher.setenv("PATH", "/usr/bin")
        env = driver.build_environment()

    path_entries = env["PATH"].split(os.pathsep)
    assert_that(path_entries[0]).ends_with(os.path.join("fixtures", "fake-claude"))
    assert_that(path_entries[1]).ends_with(os.path.join("fixtures", "fake-ruff"))
    assert_that(path_entries).contains("/usr/bin")
    assert_that(env).does_not_contain_key("NO_COLOR")
    assert_that(env["LINTRO_GLOBAL_CONFIG"]).is_equal_to("off")
    assert_that(env["COLUMNS"]).is_equal_to("120")


def test_main_reports_a_missing_binary_instead_of_forking(tmp_path: Path) -> None:
    """A missing binary must fail the verify step before any pty is opened."""
    driver = _load_driver()
    missing = tmp_path / "lintro"

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["drive_interactive_review.py", str(missing)])
        patcher.setattr(
            driver,
            "drive",
            lambda *args, **kwargs: pytest.fail("drive() must not run"),
        )
        exit_code = driver.main()

    assert_that(exit_code).is_equal_to(1)
