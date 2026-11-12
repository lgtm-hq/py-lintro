"""Exercise formatting fallback without changing the installed Black binary."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.check import check_command
from lintro.cli_utils.commands.format import format_command
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.utils import tool_snapshots
from tests.integration._tools import require_tool

pytestmark = require_tool("ruff")


@pytest.mark.parametrize(
    "black_available",
    [False, pytest.param(True, marks=require_tool("black"))],
    ids=["missing", "installed"],
)
def test_python_formatting_owner_checks_and_fixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    black_available: bool,
) -> None:
    """The runnable owner checks, fixes and reaches a stable formatting result.

    Args:
        tmp_path: Isolated fixture project.
        monkeypatch: Patch Black capability evidence without uninstalling it.
        black_available: Whether Black is allowed to run.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff.lint]\nselect = []\n[tool.lintro.execution]\nparallel = false\n",
    )
    target = tmp_path / "example.py"
    target.write_text('x = {  "a":1 }\n')
    original_probe = tool_snapshots.get_tool_snapshot

    def probe(
        name: str,
        *,
        force: bool = False,
        cache_root: Path | None = None,
    ) -> ToolSnapshot:
        """Hide Black's capability evidence only in the missing-tool case.

        Args:
            name: Requested tool.
            force: Whether to bypass cached evidence.
            cache_root: Directory anchoring the tool probe.

        Returns:
            Real tool evidence or a missing Black snapshot.
        """
        if name == "black" and not black_available:
            return ToolSnapshot(name="black", probe_error="not installed")
        return original_probe(name, force=force, cache_root=cache_root)

    monkeypatch.setattr(tool_snapshots, "get_tool_snapshot", probe)
    runner = CliRunner()
    args = [str(target), "--tools", "ruff,black", "--yes", "--no-art"]
    owner = "black" if black_available else "ruff"
    checked = runner.invoke(check_command, args)
    assert_that(checked.exit_code).is_equal_to(1)
    assert_that(checked.output).contains(f"{owner} owns Python formatting")
    if black_available:
        assert_that(checked.output).contains("ruff format_check not run")
    else:
        assert_that(checked.output).contains("black: not installed")

    explained = runner.invoke(check_command, [*args, "--explain-order"])
    assert_that(explained.exit_code).is_equal_to(0)
    assert_that(explained.output).contains(
        "Format ownership (1)",
        f"{owner} owns Python formatting",
    )
    fixed = runner.invoke(format_command, args)
    assert_that(fixed.exit_code).is_equal_to(0)
    assert_that(fixed.output).contains(f"{owner} owns Python formatting")
    assert_that(target.read_text()).is_equal_to('x = {"a": 1}\n')
    clean = runner.invoke(check_command, args)
    assert_that(clean.exit_code).is_equal_to(0)
    again = runner.invoke(format_command, args)
    assert_that(again.exit_code).is_equal_to(0)
    assert_that(target.read_text()).is_equal_to('x = {"a": 1}\n')


@require_tool("black")
def test_explanation_reports_cli_format_check_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI options override a disabled config in both explanation and execution.

    Args:
        tmp_path: Isolated project directory.
        monkeypatch: Working-directory isolation fixture.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff.lint]\nselect = []\n[tool.lintro.ruff]\nformat_check = false\n",
    )
    (tmp_path / "example.py").write_text('x = {  "a":1 }\n')
    args = [
        ".",
        "--tools",
        "ruff,black",
        "--tool-options",
        "ruff:format_check=True",
        "--yes",
    ]
    runner = CliRunner()
    explanation = runner.invoke(check_command, [*args, "--explain-order"])
    checked = runner.invoke(check_command, args)
    assert_that(explanation.exit_code).is_equal_to(0)
    assert_that(checked.exit_code).is_equal_to(1)
    for output in (explanation.output, checked.output):
        assert_that(output).contains("ruff format_check explicitly enabled")
        assert_that(output).does_not_contain("ruff format_check not run")


@require_tool("black")
@pytest.mark.parametrize("invocation", ["check", "review"])
def test_external_python_path_uses_project_probe_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation: str,
) -> None:
    """Formatting probes and execution share the target project's cache root.

    Args:
        tmp_path: Parent of unrelated cwd and target project.
        monkeypatch: Environment and cache-location controls.
        invocation: CLI check or the AI review lint bridge.
    """
    from lintro.tools.core import snapshots

    project = tmp_path / "project"
    unrelated = tmp_path / "unrelated"
    project.mkdir()
    unrelated.mkdir()
    (project / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = []\n")
    target = project / "a.py"
    target.write_text('x = {  "a":1 }\n')
    monkeypatch.chdir(unrelated)

    def cache_path(*, cache_root: Path | None = None) -> Path:
        """Use production disk placement instead of the suite's isolated default.

        Args:
            cache_root: Explicit project root, or the process cwd.

        Returns:
            Production-equivalent cache file location inside the test directory.
        """
        return ((cache_root or Path.cwd()) / snapshots.SNAPSHOT_CACHE_RELPATH).resolve()

    monkeypatch.setattr(snapshots, "_cache_file_path", cache_path)
    if invocation == "check":
        result = CliRunner().invoke(
            check_command,
            [str(target), "--tools", "ruff,black", "--yes", "--no-art"],
        )
        assert_that(result.exit_code).is_equal_to(1)
        assert_that(result.output).contains("black owns Python formatting")
    else:
        from lintro.ai.review.lint_bridge import run_lint_on_changed_files
        from lintro.config.execution_config import ExecutionConfig
        from lintro.config.lintro_config import LintroConfig

        results = run_lint_on_changed_files(
            changed_files=[str(target)],
            lintro_config=LintroConfig(
                execution=ExecutionConfig(enabled_tools=["ruff", "black"]),
            ),
        )
        assert_that({result.name for result in results}).is_equal_to({"ruff", "black"})
        assert_that(sum(result.issues_count or 0 for result in results)).is_equal_to(1)
    assert_that((unrelated / ".lintro-cache").exists()).is_false()
    assert_that((project / snapshots.SNAPSHOT_CACHE_RELPATH).exists()).is_true()


@require_tool("black")
def test_incremental_check_does_not_consume_black_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership resolution must leave Black's incremental cache untouched.

    Args:
        tmp_path: Isolated fixture project.
        monkeypatch: Working-directory isolation.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = []\n")
    (tmp_path / "a.py").write_text('x = {  "a":1 }\n')
    result = CliRunner().invoke(
        check_command,
        [".", "--tools", "ruff,black", "--incremental", "--yes"],
    )
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains(
        "Would reformat file",
        "black owns Python formatting",
    )
    assert_that(result.output).does_not_contain("No .py/.pyi files found to check")


@require_tool("black")
def test_common_check_adds_no_discovery_walks_or_subprocess_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One native discovery and one capability probe per tool suffice.

    Args:
        tmp_path: Isolated project.
        monkeypatch: Observe native file walks and actual capability probes.
    """
    from lintro.tools.core import snapshots
    from lintro.utils import path_filtering

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = []\n")
    (tmp_path / "a.py").write_text('x = {  "a":1 }\n')
    walks: list[tuple[str, ...]] = []
    probes: list[str] = []
    original_anchors = path_filtering.resolve_exclude_anchors
    original_probe = snapshots.probe_tool

    def anchors(paths: Sequence[str] | None = None) -> tuple[str, ...]:
        """Count the hook reached by every walk, including imported aliases.

        Args:
            paths: Native discovery scan paths.

        Returns:
            Real exclusion anchors for that walk.
        """
        walks.append(tuple(paths or []))
        return original_anchors(paths)

    def probe(name: str, **kwargs: Any) -> ToolSnapshot:
        """Observe a real subprocess capability probe.

        Args:
            name: Tool under inspection.
            **kwargs: Probe options.

        Returns:
            Real capability evidence.
        """
        probes.append(name)
        return original_probe(name, **kwargs)

    monkeypatch.setattr(path_filtering, "resolve_exclude_anchors", anchors)
    monkeypatch.setattr(snapshots, "probe_tool", probe)
    result = CliRunner().invoke(check_command, [".", "--tools", "ruff,black", "--yes"])
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Would reformat file")
    # Main performs two native discoveries plus its existing module-size scan.
    # The shared hook also sees any accidental ownership-resolution walk.
    assert_that(walks).is_equal_to([(".",), (".",), (".",)])
    assert_that(sorted(probes)).is_equal_to(["black", "ruff"])


@require_tool("black")
@pytest.mark.parametrize("action", ["check", "format"])
def test_unconfirmed_decision_is_not_replaced_by_a_later_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """An initial failed Black probe freezes Ruff on and Black off for this run.

    Args:
        tmp_path: Isolated project.
        monkeypatch: Inject a transient probe failure before a healthy response.
        action: Check or format invocation.
    """
    from lintro.tools.black.definition import BlackPlugin

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = []\n")
    target = tmp_path / "a.py"
    target.write_text('x = {  "a":1 }\n')
    original_probe = tool_snapshots.get_tool_snapshot
    black_probes = 0

    def probe(
        name: str,
        *,
        force: bool = False,
        cache_root: Path | None = None,
    ) -> ToolSnapshot:
        """Fail once, then expose the installed tool if incorrectly re-probed.

        Args:
            name: Tool to inspect.
            force: Probe cache bypass flag.
            cache_root: Execution directory.

        Returns:
            Real evidence on subsequent calls.

        Raises:
            RuntimeError: The first Black probe cannot be confirmed.
        """
        nonlocal black_probes
        if name == "black":
            black_probes += 1
            if black_probes == 1:
                raise RuntimeError("transient probe failure")
        return original_probe(name, force=force, cache_root=cache_root)

    def unexpected_black_write(*args: object, **kwargs: object) -> None:
        """Reject a second writer after the run committed to Ruff.

        Args:
            *args: Ignored fix arguments.
            **kwargs: Ignored fix options.

        Raises:
            AssertionError: Black was incorrectly allowed to write.
        """
        raise AssertionError("Black must not write after an unconfirmed decision")

    monkeypatch.setattr(tool_snapshots, "get_tool_snapshot", probe)
    monkeypatch.setattr(BlackPlugin, "fix", unexpected_black_write)
    command = check_command if action == "check" else format_command
    result = CliRunner().invoke(command, [".", "--tools", "ruff,black", "--yes"])
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains(
        "owner could not be confirmed",
        "black not run after probe error",
    )
    assert_that(result.output).does_not_contain(
        "format_check not run",
        "format not run",
    )
    assert_that(black_probes).is_equal_to(1)
    assert_that(target.read_text()).is_equal_to(
        'x = {  "a":1 }\n' if action == "check" else 'x = {"a": 1}\n',
    )


@pytest.mark.parametrize("strict", [False, True], ids=["permissive", "strict"])
def test_missing_black_retains_the_execution_gates_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict: bool,
) -> None:
    """Missing Black keeps canonical JSON fields, console hints and exit policy.

    Args:
        tmp_path: Isolated fixture project.
        monkeypatch: Hide Black without uninstalling it.
        strict: Missing-tool policy applied by the native execution gate.
    """
    import json

    from lintro.config.config_loader import clear_config_cache
    from lintro.enums.action import Action
    from lintro.utils.json_output import serialize_tool_result

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = []\n")
    (tmp_path / ".lintro-config.yaml").write_text(
        f"execution:\n  strict_missing_tools: {str(strict).lower()}\n",
    )
    (tmp_path / "a.py").write_text('x = {"a": 1}\n')
    clear_config_cache()
    missing = ToolSnapshot(
        name="black",
        probe_error="Tool not installed: black",
        remediation_hint="uv pip install black",
    )
    expected = tool_snapshots.snapshot_to_unavailable_result(missing, strict=strict)
    expected_fields = serialize_tool_result(expected, action=Action.CHECK)
    original_probe = tool_snapshots.get_tool_snapshot

    def probe(
        name: str,
        *,
        force: bool = False,
        cache_root: Path | None = None,
    ) -> ToolSnapshot:
        """Return real Ruff evidence and missing Black evidence.

        Args:
            name: Requested tool.
            force: Cache bypass flag.
            cache_root: Execution directory.

        Returns:
            Native evidence or the synthetic missing Black snapshot.
        """
        if name == "black":
            return missing
        return original_probe(name, force=force, cache_root=cache_root)

    monkeypatch.setattr(tool_snapshots, "get_tool_snapshot", probe)
    runner = CliRunner()
    args = [".", "--tools", "ruff,black", "--yes", "--no-art"]
    encoded = runner.invoke(check_command, [*args, "--output-format", "json"])
    assert_that(encoded.exit_code).is_equal_to(int(strict))
    black = next(
        row for row in json.loads(encoded.stdout)["results"] if row["tool"] == "black"
    )
    keys = (
        "tool",
        "success",
        "status",
        "skipped",
        "skip_reason",
        "output",
        "unavailable",
        "unavailable_reason",
    )
    assert_that({key: black[key] for key in keys}).is_equal_to(
        {key: expected_fields[key] for key in keys},
    )
    console = runner.invoke(check_command, args)
    assert_that(console.exit_code).is_equal_to(int(strict))
    assert_that(console.output).contains(expected.output, "uv pip install black")


def test_explain_order_contains_a_probe_error_without_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared boundary contains non-I/O exceptions in read-only explanation.

    Args:
        tmp_path: Isolated project directory.
        monkeypatch: Inject an unexpected Black probe failure.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.lintro]\n")
    (tmp_path / "a.py").write_text("x = 1\n")

    def fail(*args: object, **kwargs: object) -> NoReturn:
        """Raise the internal error before any formatter runs.

        Args:
            *args: Ignored probe arguments.
            **kwargs: Ignored probe options.

        Raises:
            AttributeError: Simulated internal probe bug.
        """
        raise AttributeError("broken probe internals")

    monkeypatch.setattr(tool_snapshots, "get_tool_snapshot", fail)
    result = CliRunner().invoke(
        check_command,
        [".", "--tools", "ruff,black", "--explain-order"],
    )
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains(
        "owner could not be confirmed",
        "AttributeError",
        "broken probe internals",
    )
    assert_that(result.output).does_not_contain("Traceback")
