"""Lockstep tests for the release verify step's couplings to lintro's UI.

``verify_built_binary.sh`` and ``drive_interactive_review.py`` classify a
release build by matching lintro's own human-readable copy: the ``mcp``
command's help, the interactive review prompt, and its key bindings. Those literals are hand-maintained in three other files, so without
these tests a reword inside ``lintro`` passes the whole suite and only surfaces
as a false packaging failure on the next tag (#2514).
"""

from __future__ import annotations

import importlib.util
import json
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
_FAKE_CLAUDE_PATH = (
    _REPO_ROOT / "scripts" / "build" / "fixtures" / "fake-claude" / "claude"
)

# How the fake provider recognises a fix request: it looks for this fragment in
# the `--json-schema` argument lintro sends.
_SCHEMA_SNIFF = "'\"original_code\"' in schema"

# The risk level the fixture answers with, read back out of the fixture.
_FIXTURE_RISK_PATTERN = re.compile(r'"risk_level": "([a-z-]+)"')

# The literal the verify step greps the `mcp --help` output for, as written in
# the shell script itself.
_MCP_GREP_PATTERN = re.compile(r'^MCP_HELP_MARKER="([^"]+)"$', re.MULTILINE)

# A pty is required for the send-loop tests; every CI platform has one, but
# the module must still import where it does not.
_HAS_PTY = hasattr(os, "fork") and sys.platform != "win32"

# Scripted stand-in for the binary: prints the review prompt twice, echoing
# each keypress it is sent, then exits cleanly.
_PROMPTING_CHILD = """#!/usr/bin/env python3
import os
import sys
import tty

tty.setcbreak(sys.stdin.fileno())
for _ in range(2):
    sys.stdout.write("  [y]accept group  [q]quit: ")
    sys.stdout.flush()
    key = os.read(0, 1).decode()
    sys.stdout.write("got:" + key + "\\n")
    sys.stdout.flush()
sys.exit(0)
"""

# Scripted stand-in that never prompts and never exits, so the session runs
# out of time and the child has to be killed.
_SILENT_CHILD = """#!/usr/bin/env python3
import time

time.sleep(120)
"""


def _write_child(path: Path, source: str) -> Path:
    """Write an executable stand-in for the built binary.

    Args:
        path: File to create.
        source: Python source for the stand-in.

    Returns:
        The path written, now executable.
    """
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


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
    marker = match.group(1)
    # The marker is the grep pattern, so the script must actually use it.
    assert_that(source).contains('grep -q "$MCP_HELP_MARKER"')
    return marker


def test_mcp_marker_matches_the_commands_help_output() -> None:
    """The verify step's literal must come from the ``mcp`` command's help.

    The probe is narrowed to command wiring while #2577 is open -- ``lintro
    mcp`` dies in every frozen binary -- so the pin follows it: a reword of
    the ``--workspace`` option help would otherwise fail a healthy release
    binary, discovered only on the next tag.
    """
    from click.testing import CliRunner

    from lintro.cli_utils.commands.mcp import mcp_command

    result = CliRunner().invoke(mcp_command, ["--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains(_verify_mcp_marker())


def test_bats_stub_reuses_the_same_mcp_marker() -> None:
    """The bats stub must speak the copy the verify step accepts.

    The stub hard-codes the help a real binary prints; if it drifts from the
    grep literal the suite goes green while the release gate breaks.
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


@pytest.mark.skipif(not _HAS_PTY, reason="requires a pty")
def test_drive_answers_each_prompt_once_in_order(tmp_path: Path) -> None:
    """The send loop must deliver ``d`` then ``q``, one key per prompt.

    Args:
        tmp_path: Workspace for the scripted child.
    """
    driver = _load_driver()
    binary = _write_child(tmp_path / "fake-lintro", _PROMPTING_CHILD)

    session = driver.drive(binary, tmp_path)

    assert_that(session.keys_sent).is_equal_to(len(driver.REVIEW_KEYS))
    assert_that(session.exit_code).is_equal_to(0)
    assert_that(session.timed_out).is_false()
    echoed = re.findall(rb"got:(.)", session.captured)
    assert_that(echoed).is_equal_to(list(driver.REVIEW_KEYS))


@pytest.mark.skipif(not _HAS_PTY, reason="requires a pty")
def test_drive_kills_a_child_that_never_prompts(tmp_path: Path) -> None:
    """A session that runs out of time is killed, flagged, and fails the gate.

    The flag matters on its own: a binary that rendered a diff and then hung
    would otherwise satisfy every other assertion.

    Args:
        tmp_path: Workspace for the scripted child.
    """
    driver = _load_driver()
    binary = _write_child(tmp_path / "fake-lintro", _SILENT_CHILD)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(driver, "SESSION_TIMEOUT_SECONDS", 1)
        patcher.setattr(driver, "REAP_GRACE_SECONDS", 1)
        session = driver.drive(binary, tmp_path)

        assert_that(session.keys_sent).is_equal_to(0)
        assert_that(session.captured).is_equal_to(b"")
        assert_that(session.exit_code).is_less_than(0)
        assert_that(session.timed_out).is_true()

        patcher.setattr(sys, "argv", ["drive_interactive_review.py", str(binary)])
        assert_that(driver.main()).is_equal_to(1)


@pytest.mark.skipif(not _HAS_PTY, reason="requires a pty")
def test_main_fails_a_timed_out_session_that_rendered_a_diff(
    tmp_path: Path,
) -> None:
    """A hung binary fails even when the diff and exit code look healthy.

    Args:
        tmp_path: Location for the stand-in binary path.
    """
    driver = _load_driver()
    binary = _write_child(tmp_path / "fake-lintro", _PROMPTING_CHILD)
    hung = driver.Session(
        captured=_HIGHLIGHTED_DIFF,
        keys_sent=len(driver.REVIEW_KEYS),
        exit_code=0,
        timed_out=True,
    )

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["drive_interactive_review.py", str(binary)])
        patcher.setattr(driver, "drive", lambda *args, **kwargs: hung)
        exit_code = driver.main()

    assert_that(exit_code).is_equal_to(1)


@pytest.mark.skipif(not _HAS_PTY, reason="requires a pty")
def test_main_fails_when_the_reviewed_binary_dies(tmp_path: Path) -> None:
    """An unexpected exit code fails the gate even with a rendered diff.

    Args:
        tmp_path: Location for the stand-in binary path.
    """
    driver = _load_driver()
    binary = _write_child(tmp_path / "fake-lintro", _PROMPTING_CHILD)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["drive_interactive_review.py", str(binary)])
        patcher.setattr(
            driver,
            "drive",
            lambda *args, **kwargs: driver.Session(
                captured=_HIGHLIGHTED_DIFF,
                keys_sent=len(driver.REVIEW_KEYS),
                exit_code=139,
                timed_out=False,
            ),
        )
        exit_code = driver.main()

    assert_that(exit_code).is_equal_to(1)


def test_accepted_exit_codes_cover_a_run_that_leaves_issues() -> None:
    """Quitting the review leaves issues, so ``check`` exits 1, not 0.

    Pinning both codes keeps a future tightening to ``0`` from turning every
    release into a false failure.
    """
    driver = _load_driver()

    assert_that(driver.ACCEPTED_EXIT_CODES).contains(0, 1)
    assert_that(driver.ACCEPTED_EXIT_CODES).does_not_contain(2)


def test_fake_provider_sniffs_a_field_lintro_actually_sends() -> None:
    """The fixture's schema test must match lintro's real fix schema.

    The fake provider decides between a fix array and a summary by looking for
    ``original_code`` in the ``--json-schema`` argument. If lintro renamed that
    field the fixture would answer every request with a summary, the review
    would never open, and the release gate would fail on the next tag.
    """
    from lintro.ai.cli_schemas import FIX_BATCH_CLI_SCHEMA

    fixture_source = _FAKE_CLAUDE_PATH.read_text(encoding="utf-8")
    assert_that(fixture_source).contains(_SCHEMA_SNIFF)

    assert_that(json.dumps(FIX_BATCH_CLI_SCHEMA)).contains('"original_code"')


def test_fake_provider_risk_level_is_one_lintro_accepts() -> None:
    """The fixture's risk level must be in lintro's enum, and behavioural.

    ``additionalProperties`` is false and the enum is closed, so an unknown
    value would be rejected; a *safe-style* value would route the run to the
    auto-apply fast path and the interactive review would never render a diff.
    """
    from lintro.ai.cli_schemas import FIX_BATCH_CLI_SCHEMA

    fixture_source = _FAKE_CLAUDE_PATH.read_text(encoding="utf-8")
    risk_levels = set(_FIXTURE_RISK_PATTERN.findall(fixture_source))
    assert_that(risk_levels).is_length(1)

    schema_properties = FIX_BATCH_CLI_SCHEMA["properties"]
    assert isinstance(schema_properties, dict)  # narrow type for mypy
    fixes = schema_properties["fixes"]
    assert isinstance(fixes, dict)  # narrow type for mypy
    items = fixes["items"]
    assert isinstance(items, dict)  # narrow type for mypy
    properties = items["properties"]
    assert isinstance(properties, dict)  # narrow type for mypy
    risk_level = properties["risk_level"]
    assert isinstance(risk_level, dict)  # narrow type for mypy

    assert_that(risk_level["enum"]).contains(*risk_levels)
    assert_that(risk_levels).contains("behavioral-risk")
