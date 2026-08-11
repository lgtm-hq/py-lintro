"""Tests for the built-binary registry smoke test (``#2006``).

The script under test drives a real lintro binary. These tests substitute a
tiny fake binary (a Python script) so the contract — what counts as a
populated registry — is exercised without a Nuitka build.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "smoke-test-binary.py"

_FAKE_BINARY_TEMPLATE = """#!{python}
import json
import sys

RESPONSES = json.loads({responses!r})

args = sys.argv[1:]
key = args[0] if args else ""
response = RESPONSES.get(key, {{"stdout": "", "exit": 0}})
sys.stdout.write(response["stdout"])
sys.stderr.write(response.get("stderr", ""))
sys.exit(response["exit"])
"""


def _write_fake_binary(
    *,
    path: Path,
    responses: dict[str, dict[str, object]],
) -> Path:
    """Write an executable stand-in for the lintro binary.

    Args:
        path: Destination path for the fake binary.
        responses: Mapping of first CLI argument to the stdout/stderr/exit the
            fake binary should produce.

    Returns:
        The path to the executable fake binary.
    """
    path.write_text(
        _FAKE_BINARY_TEMPLATE.format(
            python=sys.executable,
            responses=json.dumps(responses),
        ),
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _healthy_responses() -> dict[str, dict[str, object]]:
    """Build the responses of a binary with a populated tool registry.

    Returns:
        Response mapping accepted by :func:`_write_fake_binary`.
    """
    return {
        "list-tools": {
            "stdout": json.dumps(
                {
                    "ruff": {"origin": "builtin"},
                    "black": {"origin": "builtin"},
                },
            ),
            "exit": 0,
        },
        "config": {
            "stdout": json.dumps(
                {"tool_execution_order": [{"tool": "ruff", "priority": 20}]},
            ),
            "exit": 0,
        },
        "check": {"stdout": "| ruff | PASS |\n| black | PASS |\nTOTALS\n", "exit": 0},
    }


@pytest.fixture(scope="module")
def smoke() -> ModuleType:
    """Import the hyphen-named smoke-test script as a module.

    Returns:
        The imported smoke-test module.
    """
    spec = importlib.util.spec_from_file_location("smoke_test_binary", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load smoke-test script at {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["smoke_test_binary"] = module
    spec.loader.exec_module(module)
    return module


def test_healthy_binary_passes_all_checks(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A binary with a populated registry passes the smoke test.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    binary = _write_fake_binary(
        path=tmp_path / "lintro",
        responses=_healthy_responses(),
    )
    monkeypatch.setattr(sys, "argv", ["smoke-test-binary.py", str(binary)])

    assert_that(smoke.main()).is_equal_to(0)


def test_empty_registry_fails_list_tools(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """An empty ``list-tools`` payload is the #2006 failure and must fail.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["list-tools"] = {"stdout": "{}", "exit": 0}
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.list_builtin_tools(binary)).is_none()


def test_external_only_registry_fails_list_tools(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """A registry without builtins fails even when it is non-empty.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["list-tools"] = {
        "stdout": json.dumps({"third-party": {"origin": "acme-plugin"}}),
        "exit": 0,
    }
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.list_builtin_tools(binary)).is_none()


def test_invalid_list_tools_json_fails(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """Non-JSON ``list-tools`` output fails rather than being ignored.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["list-tools"] = {"stdout": "not json", "exit": 0}
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.list_builtin_tools(binary)).is_none()


def test_empty_config_execution_order_fails(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """``config`` without an execution order means no builtins were loaded.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["config"] = {
        "stdout": json.dumps({"tool_execution_order": []}),
        "exit": 0,
    }
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.check_config(binary, ["ruff", "black"])).is_equal_to(1)


def test_external_only_config_execution_order_fails(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """A non-empty order made only of third-party tools still means no builtins.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["config"] = {
        "stdout": json.dumps(
            {"tool_execution_order": [{"tool": "acme-tool", "priority": 50}]},
        ),
        "exit": 0,
    }
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.check_config(binary, ["ruff", "black"])).is_equal_to(1)


def test_skip_rows_still_prove_registry_dispatch(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """Result rows count as evidence even when every tool is skipped.

    Release runners have none of the external tool binaries installed, so every
    row is a skip — the rows themselves are what prove the registry dispatched.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["check"] = {
        "stdout": "| ruff | SKIP | - | executable not found |\nTOTALS\n",
        "exit": 0,
    }
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.check_reaches_execution(binary, ["ruff", "black"])).is_equal_to(0)


@pytest.mark.parametrize(
    ("stdout", "exit_code", "case"),
    [
        ("No tools to run.\n", 0, "empty-registry-marker"),
        ("Traceback (most recent call last):\n  boom\n", 1, "crash"),
        ("something odd\n", 3, "no-verdict-exit-code"),
        ("nothing ran here\n", 0, "no-tool-named"),
        ("Skipping ruff: executable not found\n", 0, "tool-named-in-prose-only"),
    ],
    ids=[
        "marker=no-tools-to-run",
        "outcome=crash",
        "outcome=no-verdict",
        "outcome=no-execution-evidence",
        "outcome=prose-mention-only",
    ],
)
def test_check_failures_are_reported(
    smoke: ModuleType,
    tmp_path: Path,
    stdout: str,
    exit_code: int,
    case: str,
) -> None:
    """``check`` runs that never reach a verdict fail the smoke test.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
        stdout: Output the fake binary emits for ``check``.
        exit_code: Exit code the fake binary returns for ``check``.
        case: Human-readable description of the case under test.
    """
    responses = _healthy_responses()
    responses["check"] = {"stdout": stdout, "exit": exit_code}
    binary = _write_fake_binary(path=tmp_path / f"lintro-{case}", responses=responses)

    assert_that(smoke.check_reaches_execution(binary, ["ruff", "black"])).is_equal_to(1)


def test_issues_found_still_counts_as_reaching_execution(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """Exit code 1 (issues found) proves tools ran and must pass.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["check"] = {"stdout": "| ruff | FAIL | 4 |\nTOTALS\n", "exit": 1}
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.check_reaches_execution(binary, ["ruff", "black"])).is_equal_to(0)


def test_underscore_tool_names_match_hyphenated_report(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """Registry names spelled with underscores match their report spelling.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["check"] = {"stdout": "| pip-audit | PASS |\nTOTALS\n", "exit": 0}
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.check_reaches_execution(binary, ["pip_audit"])).is_equal_to(0)


def test_missing_binary_fails(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing binary path fails instead of silently passing.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        sys,
        "argv",
        ["smoke-test-binary.py", str(tmp_path / "missing")],
    )

    assert_that(smoke.main()).is_equal_to(1)
