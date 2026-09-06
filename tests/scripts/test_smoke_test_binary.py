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
if key == "check" and ("--output-format" not in args or "json" not in args):
    sys.stderr.write("check invoked without --output-format json\\n")
    sys.exit(2)
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


# Production ``lintro check --output-format json`` shape (see
# ``lintro.utils.json_output.create_json_output``): a ``results`` array of
# per-tool objects whose ``tool`` field is the execution evidence.
HEALTHY_CHECK_JSON = json.dumps(
    {
        "results": [
            {
                "tool": "black",
                "success": False,
                "issues_count": 1,
                "skipped": False,
                "skip_reason": None,
                "timed_out": False,
                "output": "",
            },
            {
                "tool": "ruff",
                "success": True,
                "issues_count": 0,
                "skipped": False,
                "skip_reason": None,
                "timed_out": False,
                "output": "",
            },
        ],
        "summary": {
            "total_issues": 1,
            "total_fixed": 0,
            "total_remaining": 1,
            "timed_out_tools": [],
        },
    },
)

# Same document with every tool skipped, as a release runner with no external
# tool binaries installed produces.
SKIPPED_CHECK_JSON = json.dumps(
    {
        "results": [
            {
                "tool": "black",
                "success": True,
                "issues_count": 0,
                "skipped": True,
                "skip_reason": "executable not found",
                "timed_out": False,
                "output": "",
            },
            {
                "tool": "ruff",
                "success": True,
                "issues_count": 0,
                "skipped": True,
                "skip_reason": "executable not found",
                "timed_out": False,
                "output": "",
            },
        ],
        "summary": {
            "total_issues": 0,
            "total_fixed": 0,
            "total_remaining": 0,
            "timed_out_tools": [],
        },
    },
)


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
        "check": {"stdout": HEALTHY_CHECK_JSON, "exit": 0},
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
    monkeypatch.setattr(
        smoke,
        "BUILTIN_INDEX_PATH",
        _write_fake_index(path=tmp_path / "_builtin_index.py", names=["black", "ruff"]),
    )
    monkeypatch.setattr(sys, "argv", ["smoke-test-binary.py", str(binary)])

    assert_that(smoke.main()).is_equal_to(0)


def _write_fake_index(*, path: Path, names: list[str]) -> Path:
    """Write a stand-in generated builtin index.

    Args:
        path: Destination path for the index module.
        names: Package names to declare as registering a tool.

    Returns:
        The path the index was written to.
    """
    rendered = "".join(f'    "{name}",\n' for name in names)
    path.write_text(
        "BUILTIN_TOOL_MODULES: tuple[str, ...] = (\n"
        f"{rendered})\n\n"
        "REGISTERING_TOOL_PACKAGES: tuple[str, ...] = (\n"
        f"{rendered})\n",
    )
    return path


def test_expected_builtin_tools_reads_the_registering_subset(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """The expected tool set comes from the index's registering package set.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    index = _write_fake_index(
        path=tmp_path / "_builtin_index.py",
        names=["black", "ruff"],
    )

    assert_that(smoke.expected_builtin_tools(index)).is_equal_to(["black", "ruff"])


def test_expected_builtin_tools_fails_closed_on_a_missing_index(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """A missing index fails the smoke test instead of skipping completeness.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    with pytest.raises(RuntimeError, match="could not read"):
        smoke.expected_builtin_tools(tmp_path / "absent.py")


def test_partial_registry_fails_list_tools(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """A registry missing indexed builtins fails, not just an empty one.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    binary = _write_fake_binary(
        path=tmp_path / "lintro",
        responses=_healthy_responses(),
    )

    result = smoke.list_builtin_tools(binary, ["black", "ruff", "yamllint"])

    assert_that(result).is_none()


def test_hyphenated_report_names_satisfy_underscore_expectations(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """Expected ``pip_audit`` is satisfied by a reported ``pip-audit``.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["list-tools"] = {
        "stdout": json.dumps({"pip-audit": {"origin": "builtin"}}),
        "exit": 0,
    }
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.list_builtin_tools(binary, ["pip_audit"])).is_equal_to(
        ["pip-audit"],
    )


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

    assert_that(smoke.list_builtin_tools(binary, [])).is_none()


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

    assert_that(smoke.list_builtin_tools(binary, [])).is_none()


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

    assert_that(smoke.list_builtin_tools(binary, [])).is_none()


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
    """JSON results count as evidence even when every tool is skipped.

    Release runners have none of the external tool binaries installed, so every
    result is a skip — the result objects themselves prove the registry
    dispatched.

    Args:
        smoke: Imported smoke-test module.
        tmp_path: Pytest-provided temporary directory.
    """
    responses = _healthy_responses()
    responses["check"] = {"stdout": SKIPPED_CHECK_JSON, "exit": 0}
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
        (
            json.dumps({"results": [], "summary": {"total_issues": 0}}),
            0,
            "empty-results-array",
        ),
        (json.dumps(["ruff", "black"]), 0, "json-array"),
    ],
    ids=[
        "marker=no-tools-to-run",
        "outcome=crash",
        "outcome=no-verdict",
        "outcome=no-execution-evidence",
        "outcome=prose-mention-only",
        "outcome=empty-json-results",
        "outcome=json-array",
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
    responses["check"] = {"stdout": HEALTHY_CHECK_JSON, "exit": 1}
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
    responses["check"] = {
        "stdout": json.dumps(
            {
                "results": [
                    {
                        "tool": "pip-audit",
                        "success": True,
                        "issues_count": 0,
                        "skipped": False,
                        "skip_reason": None,
                        "timed_out": False,
                        "output": "",
                    },
                ],
                "summary": {
                    "total_issues": 0,
                    "total_fixed": 0,
                    "total_remaining": 0,
                    "timed_out_tools": [],
                },
            },
        ),
        "exit": 0,
    }
    binary = _write_fake_binary(path=tmp_path / "lintro", responses=responses)

    assert_that(smoke.check_reaches_execution(binary, ["pip_audit"])).is_equal_to(0)


def test_matches_tool_requires_complete_identifier(smoke: ModuleType) -> None:
    """A longer tool name must not satisfy a shorter builtin.

    Args:
        smoke: Imported smoke-test module.
    """
    assert_that(smoke._matches_tool(text="ruff-format", name="ruff")).is_false()
    assert_that(smoke._matches_tool(text="ruff", name="ruff")).is_true()
    assert_that(smoke._matches_tool(text="pip-audit", name="pip_audit")).is_true()
    assert_that(
        smoke._matches_tool(text="| ruff-format | PASS |", name="ruff"),
    ).is_false()
    assert_that(
        smoke._matches_tool(text="| ruff | PASS |", name="ruff"),
    ).is_true()


def test_skip_reason_cannot_satisfy_a_different_tool(smoke: ModuleType) -> None:
    """A skip_reason mentioning another tool is not a result for that tool.

    Args:
        smoke: Imported smoke-test module.
    """
    payload = {
        "results": [
            {
                "tool": "black",
                "success": True,
                "issues_count": 0,
                "skipped": False,
                "skip_reason": "unable to open ruff.py",
                "timed_out": False,
                "output": "",
            },
            {
                "tool": "ruff-format",
                "success": True,
                "issues_count": 0,
                "skipped": False,
                "skip_reason": None,
                "timed_out": False,
                "output": "",
            },
        ],
    }
    found = smoke._tools_in_check_json(
        payload=payload,
        builtin_tools=["ruff", "black"],
    )
    assert_that(found).is_equal_to(["black"])


def test_table_scraping_helpers_are_gone(smoke: ModuleType) -> None:
    """The smoke test must not scrape the default result table.

    Args:
        smoke: Imported smoke-test module.
    """
    assert_that(hasattr(smoke, "_tools_in_result_table")).is_false()
    assert_that(hasattr(smoke, "_result_table_tool_cell")).is_false()


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
