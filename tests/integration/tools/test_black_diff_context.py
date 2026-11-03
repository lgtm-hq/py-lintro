"""Real Black reports must distinguish Python string contents from diagnostics."""

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.parsers.black.black_parser import (
    black_report_parse_failures,
    parse_black_output,
)
from lintro.tools.black.definition import BlackPlugin
from lintro.utils.execution.baseline_filter import apply_baseline
from lintro.utils.fingerprint_baseline import snapshot
from tests.integration._tools import require_tool

# The production plugin measures E501 with Ruff after each completed Black pass.
pytestmark = [require_tool("black"), require_tool("ruff")]


@pytest.fixture(
    params=[
        ("context", "\n"),
        ("context", "\u2028"),
        ("context", "\x0c"),
        ("changed", "\u2028"),
        ("changed", "\x0c"),
    ],
    ids=[
        "lf-context",
        "unicode-context",
        "ff-context",
        "unicode-changed",
        "ff-changed",
    ],
)
def string_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Path:
    """Create the reproduced valid string without executing the source payload."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lintro-config.yaml").write_text(
        "execution:\n  auto_install_deps: false\n",
        encoding="utf-8",
    )
    file = tmp_path / "example.py"
    row_kind, separator = request.param
    start, end = (
        ('message = """', '"""') if row_kind == "context" else ("message='''", "'''")
    )
    file.write_text(
        f"{start}head{separator}reformatted phantom.py\n"
        f"would reformat ghost.py{separator}9 files would be reformatted.\n"
        f"{end}\nx=1\n",
        encoding="utf-8",
    )
    return file


def test_real_diff_string_context_keeps_only_actual_file(string_file: Path) -> None:
    """A completed preview preserves bytes and measures only the actual file."""
    before = string_file.read_bytes()
    plugin = BlackPlugin()
    native = plugin._run_subprocess_result(
        cmd=[
            *plugin._get_executable_command(tool_name="black"),
            "--diff",
            string_file.name,
        ],
        timeout=30,
        cwd=str(string_file.parent),
    )
    parsed = parse_black_output(native.output)
    plugin.set_options(diff=True)
    result = plugin.fix([str(string_file)], {})
    assert_that(native.returncode).is_zero()
    assert_that(native.stdout).contains(
        "reformatted phantom.py",
        "would reformat ghost.py",
        "9 files would be reformatted.",
        "\n-x=1\n",
        "\n+x = 1\n",
    )
    if before.startswith(b"message='''"):
        assert_that(native.stdout).contains(
            "\n-message='''head",
            '\n+message = """head',
        )
    else:
        assert_that(native.stdout).contains('\n message = """head')
    assert_that(native.stderr).contains("would reformat example.py")
    assert_that(result.issues_count).is_equal_to(1)
    assert_that([issue.file for issue in parsed]).is_equal_to([string_file.name])
    assert_that(black_report_parse_failures(native.output, parsed)).is_zero()
    # The preview and final check both describe this one still-unformatted file.
    assert_that([issue.file for issue in result.issues]).is_equal_to(
        [string_file.name, string_file.name],
    )
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_zero()
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.success).is_false()
    assert_that(string_file.read_bytes()).is_equal_to(before)


def test_real_string_mutation_and_clean_preview_keep_measured_counts(
    string_file: Path,
) -> None:
    """Real mutation fixes one file; its following preview is clean and read-only."""
    before = string_file.read_bytes()
    plugin = BlackPlugin()
    result = plugin.fix([str(string_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_zero()
    assert_that(result.initial_issues).extracting("file").is_equal_to(
        [string_file.name],
    )
    assert_that(result.parse_failures_count).is_none()
    formatted = string_file.read_bytes()
    expected = (
        before.replace(b"x=1\n", b"x = 1\n")
        .replace(b"message='''", b'message = """')
        .replace(b"\n'''\n", b'\n"""\n')
    )
    assert_that(formatted).is_equal_to(expected)
    plugin.set_options(diff=True)
    clean = plugin.fix([str(string_file)], {})
    assert_that(clean.success).is_true()
    assert_that(clean.issues).is_empty()
    assert_that(clean.issues_count).is_zero()
    assert_that(clean.initial_issues_count).is_zero()
    assert_that(clean.fixed_issues_count).is_zero()
    assert_that(clean.remaining_issues_count).is_zero()
    assert_that(string_file.read_bytes()).is_equal_to(formatted)


@pytest.mark.parametrize("diff", [False, True], ids=["mutation-abort", "diff-failure"])
def test_real_string_and_syntax_error_preserve_failure_and_baseline(
    string_file: Path,
    diff: bool,
) -> None:
    """A real syntax failure remains visible beside the actual formatting finding."""
    before = string_file.read_bytes()
    plugin = BlackPlugin()
    plugin.set_options(_baseline_coverage=True)
    checked = plugin.check([str(string_file)], {})
    assert_that(checked.inspected_paths).is_equal_to(frozenset({str(string_file)}))
    baseline = snapshot([checked], root=string_file.parent)
    assert_that(baseline.entries).is_length(1)
    broken = string_file.with_name("broken.py")
    broken.write_text("def f(:\n", encoding="utf-8")
    plugin.set_options(diff=diff)

    result = plugin.fix([str(string_file), str(broken)], {})

    assert_that((result.output or "").lower()).contains(
        "error: cannot",
        "parse",
        "broken.py",
        "def f(:",
    )
    assert_that(result.issues).extracting("file").is_equal_to([string_file.name])
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.inspected_paths).is_none()
    assert_that(result.findings_complete).is_false()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.initial_issues_count).is_none()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(snapshot([result], root=string_file.parent).entries).is_empty()
    artifact = RunArtifact(
        tool_results=[result],
        action=Action.FIX,
        workspace_root=string_file.parent,
        total_issues=1,
        exit_code=1,
    )
    apply_baseline(artifact, baseline)
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(artifact.tool_results[0].baselined_count).is_zero()
    assert_that(artifact.tool_results[0].output).is_equal_to(result.output)
    assert_that(string_file.read_bytes()).is_equal_to(before)
    assert_that(broken.read_text(encoding="utf-8")).is_equal_to("def f(:\n")
