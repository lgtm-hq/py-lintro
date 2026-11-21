"""A path named like an option is guarded for the tool and reported bare (#3584)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue, strip_option_guard
from lintro.parsers.pylint import parse_pylint_output
from lintro.parsers.pylint.pylint_issue import PylintIssue
from lintro.parsers.typos.typos_parser import parse_typos_report
from lintro.plugins.execution_preparation import (
    PreparedExecution,
    prepare_execution,
)
from lintro.plugins.registry import ToolRegistry
from lintro.utils.findings import finding_from_issue
from lintro.utils.fingerprint_baseline import fingerprint
from lintro.utils.option_paths import option_safe_path, option_safe_paths
from lintro.utils.output.sarif.bridge import standard_issues_from_results
from lintro.utils.output.sarif.document import to_sarif


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("-", "./-"),
        ("--", "./--"),
        ("-e.sh", "./-e.sh"),
        ("--config=lint.ignore=['ALL']#.py", "./--config=lint.ignore=['ALL']#.py"),
        ("--dir=x/a.py", "./--dir=x/a.py"),
        ("a.py", "a.py"),
        ("src/-e.py", "src/-e.py"),
        ("a=b.py", "a=b.py"),
        ("/abs/-e.py", "/abs/-e.py"),
        ("./-e.py", "./-e.py"),
        ("", ""),
    ],
)
def test_option_safe_path_guards_only_a_leading_dash(path: str, expected: str) -> None:
    """Only a relative path starting with ``-`` is prefixed.

    Args:
        path: Candidate argument.
        expected: Argument lintro hands to the tool.
    """
    assert_that(option_safe_path(path)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("./-", "-"),
        ("./--", "--"),
        ("./--force-exclude=.py", "--force-exclude=.py"),
        (".\\-e.py", "-e.py"),
        ("./a.py", "./a.py"),
        ("./src/-e.py", "./src/-e.py"),
        ("-e.py", "-e.py"),
        ("/abs/-e.py", "/abs/-e.py"),
    ],
)
def test_strip_option_guard_removes_only_lintros_guard(
    reported: str,
    expected: str,
) -> None:
    """A tool's ``./-name`` maps back to ``-name``; other paths are untouched.

    Args:
        reported: Path as the tool printed it.
        expected: Path lintro records.
    """
    assert_that(strip_option_guard(reported)).is_equal_to(expected)


def test_guard_round_trips_every_option_like_name() -> None:
    """Guarding then stripping returns the name the user sees."""
    names = ["-", "--", "-n", "--config=x.py", "-e.sh", "-.toml"]
    guarded = option_safe_paths(names)
    assert_that(guarded).does_not_contain(*names)
    assert_that([strip_option_guard(path) for path in guarded]).is_equal_to(names)


def test_issue_file_drops_the_guard_on_construction_and_assignment() -> None:
    """Every issue, including subclasses with their own init, records ``-name``."""
    built = PylintIssue(file="./-e.py", line=1, column=0, message="m", code="C0114")
    assert_that(built.file).is_equal_to("-e.py")
    assert_that(built.severity).is_not_none()
    plain = BaseIssue(file="./a.py")
    assert_that(plain.file).is_equal_to("./a.py")
    plain.file = "./--"
    assert_that(plain.file).is_equal_to("--")


def test_parsers_report_the_unguarded_name() -> None:
    """Real parser output naming ``./-`` yields findings for ``-``."""
    typos = parse_typos_report(
        json.dumps(
            {
                "type": "typo",
                "path": "./-",
                "line_num": 1,
                "byte_offset": 0,
                # Built at runtime so the repository's typos pass keeps it.
                "typo": "t" + "eh",
                "corrections": ["the"],
            },
        ),
    )
    pylint = parse_pylint_output(
        json.dumps(
            {
                "messages": [
                    {
                        "type": "warning",
                        "symbol": "unused-import",
                        "message": "Unused import os",
                        "messageId": "W0611",
                        "confidence": "UNDEFINED",
                        "module": "x",
                        "obj": "",
                        "line": 2,
                        "column": 0,
                        "path": "./--ignore-patterns=.*.py",
                        "absolutePath": "/repo/--ignore-patterns=.*.py",
                    },
                ],
                "statistics": {"modulesLinted": 1, "score": 5.0},
            },
        ),
    )
    assert_that([issue.file for issue in typos.issues]).is_equal_to(["-"])
    assert_that([issue.file for issue in pylint]).is_equal_to(
        ["--ignore-patterns=.*.py"],
    )


def test_findings_sarif_and_baselines_use_the_unguarded_name(
    tmp_path: Path,
) -> None:
    """The guard never reaches findings, SARIF URIs or baseline fingerprints.

    Args:
        tmp_path: Workspace root holding the planted file.
    """
    (tmp_path / "-e.py").write_text("", encoding="utf-8")
    guarded = BaseIssue(file="./-e.py", line=1, message="m")
    bare = BaseIssue(file="-e.py", line=1, message="m")

    assert_that(finding_from_issue(issue=guarded, tool_name="ruff").file).is_equal_to(
        "-e.py",
    )
    result = ToolResult(name="ruff", success=False, issues_count=1, issues=[guarded])
    sarif = to_sarif(standard_issues_from_results([result]))
    uris = [
        location["physicalLocation"]["artifactLocation"]["uri"]
        for run in sarif["runs"]
        for entry in run["results"]
        for location in entry["locations"]
    ]
    assert_that(uris).is_equal_to(["-e.py"])
    from_guarded = fingerprint(guarded, tool="ruff", root=tmp_path, cwd=str(tmp_path))
    from_bare = fingerprint(bare, tool="ruff", root=tmp_path, cwd=str(tmp_path))
    assert_that(from_guarded).is_not_none()
    assert_that(from_guarded).is_equal_to(from_bare)
    assert_that(from_guarded.file if from_guarded else "").is_equal_to("-e.py")


def test_prepare_execution_guards_only_option_like_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared file list prefixes ``-`` names and leaves the rest as they were.

    Args:
        tmp_path: Project with ordinary and option-like files.
        monkeypatch: Fixture for the working directory and version gate.
    """
    for name in ("a.py", "-.py", "--config=x.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / "--dir=x").mkdir()
    (tmp_path / "--dir=x" / "b.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_args, **_kwargs: None,
    )
    plugin = type(ToolRegistry.get("ruff"))()
    prepared = prepare_execution(
        paths=["."],
        options={},
        definition=plugin.definition,
        exclude_patterns=[],
        include_venv=False,
        current_options={},
    )
    assert isinstance(prepared, PreparedExecution)
    assert_that(sorted(prepared.rel_files)).is_equal_to(
        sorted(["a.py", "./-.py", "./--config=x.py", "./--dir=x/b.py"]),
    )
    assert_that(
        all(Path(file).is_absolute() for file in prepared.files),
    ).is_true()
