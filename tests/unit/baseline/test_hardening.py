"""Hardening follow-ups for fingerprint baselines (#3351)."""

from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import (
    build_config_from_dict,
    clear_config_cache,
    load_config,
)
from lintro.config.lintro_config import LintroConfig
from lintro.exceptions.errors import ConfigurationError
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.bandit.bandit_issue import BanditIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.utils.fingerprint_baseline import (
    filter_issues,
    fingerprint,
    maintain_baseline,
    snapshot,
)
from lintro.utils.fingerprint_baseline_file import (
    change_baseline,
    load_run_baseline,
    read_baseline,
    require_baseline_root,
)
from lintro.utils.tool_options import merge_private_tool_options


def _syntax_error(file: str = "src/a.py") -> RuffIssue:
    """Return Ruff's report for a file it could not parse.

    Args:
        file: Diagnostic path.

    Returns:
        A Ruff ``invalid-syntax`` finding.
    """
    return RuffIssue(
        file=file,
        line=2,
        code="invalid-syntax",
        message="Expected `)`, found newline",
    )


def test_user_cannot_supply_reserved_coverage_option() -> None:
    """A user ``_baseline_coverage`` key is dropped; internal options still apply."""
    merged, dropped = merge_private_tool_options(
        {"ruff": {"_baseline_coverage": False, "select": ["F"]}, "mypy": {}},
        {"ruff": {"_baseline_coverage": True}, "black": {"_baseline_coverage": True}},
    )

    assert_that(dropped).is_equal_to(["ruff:_baseline_coverage"])
    assert_that(merged).is_equal_to(
        {
            "ruff": {"select": ["F"], "_baseline_coverage": True},
            "black": {"_baseline_coverage": True},
        },
    )


def test_user_reserved_option_is_ignored_without_internal_options() -> None:
    """Ordinary runs never forward a user-injected coverage request."""
    merged, dropped = merge_private_tool_options(
        {"black": {"_baseline_coverage": True}},
        None,
    )

    assert_that(merged).is_empty()
    assert_that(dropped).is_equal_to(["black:_baseline_coverage"])


def test_management_scan_keeps_coverage_out_of_user_tool_options(
    workspace: Path,
    dirty_result: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scan forwards user options verbatim and coverage requests privately."""
    received: dict[str, Any] = {}

    def fake_execute_run(**kwargs: Any) -> RunArtifact:
        received.update(kwargs)
        return RunArtifact(tool_results=[dirty_result], workspace_root=workspace)

    monkeypatch.setattr(
        "lintro.cli_utils.commands.baseline.execute_run",
        fake_execute_run,
    )

    result = CliRunner().invoke(
        cli,
        ["baseline", "create", "--tools", "ruff", "--tool-options", "ruff:select=F"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(received["tool_options"]).is_equal_to("ruff:select=F")
    assert_that(received["private_tool_options"]).is_equal_to(
        {
            "ruff": {"_baseline_coverage": True},
            "black": {"_baseline_coverage": True},
        },
    )


def test_bandit_findings_with_different_text_get_different_fingerprints(
    workspace: Path,
) -> None:
    """Bandit's message carries ``issue_text``, so it stays part of the identity."""
    first = BanditIssue(file="a.py", line=1, test_id="B101", issue_text="one")
    second = BanditIssue(file="a.py", line=1, test_id="B101", issue_text="two")

    one = fingerprint(first, tool="bandit", root=workspace)
    two = fingerprint(second, tool="bandit", root=workspace)

    assert one is not None
    assert two is not None
    assert_that(one.message_hash).is_not_equal_to(two.message_hash)


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX-only")
@pytest.mark.parametrize("create", [True, False])
def test_writer_flushes_parent_directory_after_commit(
    workspace: Path,
    dirty_result: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
    create: bool,
) -> None:
    """The rename or link is followed by an fsync of the parent directory."""
    baseline = snapshot([dirty_result], root=workspace)
    path = workspace / ".lintro-baseline.json"
    if not create:
        change_baseline(path, lambda _: baseline, create=True)
    flushed: list[tuple[bool, int, bool]] = []
    real_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        info = os.fstat(descriptor)
        flushed.append((stat.S_ISDIR(info.st_mode), info.st_ino, path.exists()))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    change_baseline(path, lambda _: baseline, create=create)

    directory_flushes = [entry for entry in flushed if entry[0]]
    assert_that(directory_flushes).is_equal_to(
        [(True, workspace.stat().st_ino, True)],
    )
    assert_that(flushed[-1][0]).is_true()


def test_concurrent_writer_cannot_change_file_during_management_scan(
    workspace: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock covers the scan, so a write made during it cannot be lost."""
    path = workspace / ".lintro-baseline.json"
    initial = snapshot([dirty_result], root=workspace)
    change_baseline(path, lambda _: initial, create=True)
    added = snapshot(
        [replace(dirty_result, issues=[replace(finding, code="F999")])],
        root=workspace,
    )
    concurrent: dict[str, str] = {}

    def scan_with_concurrent_writer(**_: Any) -> RunArtifact:
        try:
            change_baseline(path, lambda _: added)
        except ValueError as exc:
            concurrent["error"] = str(exc)
        fixed = ToolResult(
            name="ruff",
            success=True,
            inspected_files=True,
            inspected_paths=frozenset({finding.file}),
        )
        return RunArtifact(tool_results=[fixed], workspace_root=workspace)

    monkeypatch.setattr(
        "lintro.cli_utils.commands.baseline.execute_run",
        scan_with_concurrent_writer,
    )

    result = CliRunner().invoke(cli, ["baseline", "update", "--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(concurrent.get("error", "")).contains("Baseline lock already exists")
    assert_that(read_baseline(path).entries).is_empty()
    assert_that(Path(f"{path}.lock").exists()).is_false()


def test_source_parse_diagnostic_is_never_acknowledged(
    workspace: Path,
    dirty_result: ToolResult,
) -> None:
    """A syntax error stays out of snapshots and stays visible under filtering."""
    broken = replace(
        dirty_result,
        issues=[_syntax_error(), *(dirty_result.issues or [])],
        issues_count=2,
    )
    created = snapshot([broken], root=workspace)
    entry = fingerprint(_syntax_error(), tool="ruff", root=workspace)
    assert entry is not None
    forged = created.model_copy(update={"entries": [*created.entries, entry]})

    retained, acknowledged = filter_issues(
        broken.issues or [],
        result=broken,
        baseline=forged,
        root=workspace,
    )

    assert_that([item.code for item in created.entries]).is_equal_to(["F401"])
    assert_that([issue.get_code() for issue in retained]).is_equal_to(
        ["invalid-syntax"],
    )
    assert_that(acknowledged).is_equal_to(1)


@pytest.mark.parametrize("prune", [True, False])
def test_unparsed_file_keeps_its_acknowledgements(
    workspace: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
    prune: bool,
) -> None:
    """A file Ruff could not parse supplies no coverage, so nothing is removed."""
    previous = snapshot([dirty_result], root=workspace)
    unparsed = replace(
        dirty_result,
        issues=[_syntax_error(finding.file)],
        issues_count=1,
        inspected_paths=frozenset({finding.file}),
    )

    merged = maintain_baseline(
        previous,
        snapshot([unparsed], root=workspace),
        results=[unparsed],
        root=workspace,
        prune=prune,
    )

    assert_that(merged.entries).is_equal_to(previous.entries)


def test_configured_path_without_project_config_roots_at_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A global ``sub/x.json`` resolves at cwd, so cwd is also its root."""
    monkeypatch.chdir(tmp_path)
    config = LintroConfig(baseline="sub/x.json")

    configured = require_baseline_root(
        tmp_path / "sub" / "x.json",
        config,
        explicit=None,
    )

    assert_that(configured).is_equal_to(tmp_path.resolve())
    with pytest.raises(ValueError, match="project root"):
        require_baseline_root(
            tmp_path / "sub" / "x.json",
            config,
            explicit="sub/x.json",
        )


def _global_baseline_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> tuple[LintroConfig, Path]:
    """Load a config whose only ``baseline`` key comes from a global file.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
        value: Global ``baseline`` value.

    Returns:
        The loaded configuration and the project directory (now cwd).
    """
    global_file = tmp_path / "global" / "lintro.yaml"
    global_file.parent.mkdir()
    global_file.write_text(f"baseline: {value}\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", str(global_file))
    clear_config_cache()
    return load_config(allow_pyproject_fallback=False), project


def test_global_subdirectory_baseline_loads_from_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dirty_result: ToolResult,
) -> None:
    """With no project config, a global ``sub/x.json`` is usable from cwd."""
    config, project = _global_baseline_config(tmp_path, monkeypatch, "sub/x.json")
    (project / "sub").mkdir()
    expected = snapshot([dirty_result], root=project)
    change_baseline(project / "sub" / "x.json", lambda _: expected, create=True)

    loaded = load_run_baseline(None, config)

    assert_that(config.global_contributed_keys).contains("baseline")
    assert loaded is not None
    assert_that(loaded.entries).is_equal_to(expected.entries)


def test_missing_global_baseline_error_names_global_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing file configured only globally names the global config file."""
    config, _ = _global_baseline_config(tmp_path, monkeypatch, "x.json")

    with pytest.raises(ConfigurationError) as raised:
        load_run_baseline(None, config)

    message = str(raised.value)
    assert_that(message).contains("Cannot load fingerprint baseline")
    assert_that(message).contains(str(tmp_path / "global" / "lintro.yaml"))
    assert_that(load_run_baseline(False, config)).is_none()


@pytest.mark.parametrize(
    ("value", "actual"),
    [(5, "int"), ("", "an empty string"), (None, "null"), (["a"], "list")],
)
def test_wrong_typed_baseline_value_is_a_one_line_error(
    value: object,
    actual: str,
) -> None:
    """Bad ``baseline`` values fail like other keys, without a pydantic dump."""
    with pytest.raises(ValueError) as raised:
        build_config_from_dict({"baseline": value})

    assert_that(str(raised.value)).is_equal_to(
        f"baseline must be a non-empty file path string, got {actual}",
    )


def test_watch_no_baseline_disables_configured_file(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--no-baseline`` starts watching although the configured file is missing."""
    (workspace / ".lintro-config.yaml").write_text(
        "baseline: missing.json\n",
        encoding="utf-8",
    )
    clear_config_cache()
    started: dict[str, Any] = {}

    def fake_watch_paths(*_args: Any, on_batch: Any, **_kwargs: Any) -> None:
        started["baseline"] = on_batch.__self__.baseline

    monkeypatch.setattr("lintro.watch.watcher.watch_paths", fake_watch_paths)

    refused = CliRunner().invoke(cli, ["watch", "."])
    allowed = CliRunner().invoke(cli, ["watch", "--no-baseline", "."])

    assert_that(refused.exit_code).is_equal_to(1)
    assert_that(refused.output).contains("Cannot load fingerprint baseline")
    assert_that(refused.output).contains("--no-baseline")
    assert_that(allowed.exit_code).is_equal_to(0)
    assert_that(started).is_equal_to({"baseline": False})


def test_simple_runner_accepts_false_to_skip_configured_baseline(
    workspace: Path,
    dirty_result: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_lint_tools_simple(baseline=False)`` never loads the configured file."""
    from lintro.utils.tool_executor import run_lint_tools_simple

    (workspace / ".lintro-config.yaml").write_text(
        "baseline: missing.json\n",
        encoding="utf-8",
    )
    clear_config_cache()
    monkeypatch.setattr(
        "lintro.utils.tool_executor.execute_run",
        lambda **_: RunArtifact(
            tool_results=[dirty_result],
            workspace_root=workspace,
            exit_code=1,
        ),
    )
    common: dict[str, Any] = {
        "action": "check",
        "paths": ["."],
        "tools": "ruff",
        "tool_options": None,
        "exclude": None,
        "include_venv": False,
        "group_by": "file",
        "output_format": "json",
        "verbose": False,
        "render_summary": False,
    }

    with pytest.raises(ConfigurationError, match="missing.json"):
        run_lint_tools_simple(**common)

    assert_that(run_lint_tools_simple(**common, baseline=False)).is_equal_to(1)
