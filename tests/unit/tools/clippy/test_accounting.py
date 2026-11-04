"""Clippy accounting through discovery, execution, parsing and result construction."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - completed process fixtures only
from collections.abc import Sequence
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.clippy.definition import ClippyPlugin

_VALID = json.dumps(
    {
        "reason": "compiler-message",
        "message": {
            "code": {"code": "clippy::needless_return"},
            "message": "kept lint",
            "level": "warning",
            "spans": [{"file_name": "src/main.rs", "line_start": 1}],
        },
    },
)
_BROKEN = '{"reason":"compiler-message","message":null}'
_COMPILER_ERROR = json.dumps(
    {
        "reason": "compiler-message",
        "message": {
            "code": {"code": "E0425"},
            "message": "cannot find value missing",
            "level": "error",
            "spans": [],
        },
    },
)


def _run(
    plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: str,
    stages: Sequence[tuple[int, str] | subprocess.TimeoutExpired],
) -> ToolResult:
    """Replace only process completion, keeping the actual tool consumer.

    Args:
        plugin: Version-gated Clippy instance.
        monkeypatch: Process-boundary patch fixture.
        tmp_path: Real project used by discovery and Cargo root selection.
        method: Check or fix entry point.
        stages: Exit status and stdout for each process completion.

    Returns:
        The actual plugin's result.
    """
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname="accounting"\nversion="0.1.0"\n',
    )
    source = tmp_path / "src" / "main.rs"
    source.parent.mkdir()
    source.write_text("fn main() {}\n")
    completions = iter(stages)

    def completed(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str | bytes]:
        """Complete a command using the next native transport fixture."""
        completion = next(completions)
        if isinstance(completion, subprocess.TimeoutExpired):
            raise completion
        status, output = completion
        text = kwargs.get("text", kwargs.get("universal_newlines", False))
        return subprocess.CompletedProcess(
            args,
            status,
            output if text else output.encode(),
            "" if text else b"",
        )

    monkeypatch.setattr("subprocess.run", completed)
    execute = plugin.fix if method == "fix" else plugin.check
    return execute([str(source)], {})


@pytest.mark.parametrize("with_valid", [False, True], ids=["corrupt", "mixed"])
def test_check_corruption_fails_closed(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    with_valid: bool,
) -> None:
    """A zero native exit cannot conceal a dropped Clippy diagnostic."""
    output = "\n".join([_VALID, _BROKEN] if with_valid else [_BROKEN])
    result = _run(clippy_plugin, monkeypatch, tmp_path, "check", [(0, output)])

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues_count).is_equal_to(int(with_valid))
    assert_that(result.output).contains(_BROKEN)
    assert_that(result.inspected_files).is_none()
    assert_that(result.initial_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()


@pytest.mark.parametrize("stage", [0, 1, 2], ids=["initial", "mutation", "verify"])
@pytest.mark.parametrize("with_valid", [False, True], ids=["corrupt", "mixed"])
def test_fix_corruption_never_invents_complete_counts(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: int,
    with_valid: bool,
) -> None:
    """Corruption in each phase keeps usable findings and unknown measurements."""
    output = "\n".join([_VALID, _BROKEN] if with_valid else [_BROKEN])
    stages = [(0, _VALID), (0, ""), (0, "")]
    stages[stage] = (0, output)
    result = _run(clippy_plugin, monkeypatch, tmp_path, "fix", stages)

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(None if stage == 0 else 1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.output).contains(_BROKEN)
    if with_valid:
        assert_that(result.issues).extracting("message").contains("kept lint")


@pytest.mark.parametrize("stage", [0, 1, 2], ids=["initial", "mutation", "verify"])
@pytest.mark.parametrize(
    "output",
    [
        "error: invalid configuration",
        _COMPILER_ERROR,
        _VALID + "\n" + _COMPILER_ERROR,
        _VALID + "\nerror: failed to run custom build command for `broken v0.1.0`",
        _VALID + "\nerror: failed to parse manifest at `/workspace/broken/Cargo.toml`",
    ],
    ids=[
        "config",
        "compiler",
        "compiler-and-lint",
        "build-script-and-lint",
        "manifest-and-lint",
    ],
)
def test_fix_native_failure_stays_independent_of_lint_counts(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: int,
    output: str,
) -> None:
    """Compilation/configuration failures cannot be converted into a clean fix."""
    stages = [(0, _VALID), (0, ""), (0, "")]
    stages[stage] = (101, output)
    result = _run(clippy_plugin, monkeypatch, tmp_path, "fix", stages)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.parse_failures_count or 0).is_zero()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.initial_issues_count).is_equal_to(None if stage == 0 else 1)
    assert_that(result.output).contains(output)


@pytest.mark.parametrize("method", ["check", "fix"])
@pytest.mark.parametrize(
    "output",
    [
        "",
        "   \n",
        'Checking demo\n{"reason":"compiler-artifact","fresh":true}\n'
        '{"reason":"build-finished","success":true}',
        '{"reason":"build-script-executed","out_dir":"target/build"}',
        json.dumps(
            {
                "reason": "compiler-message",
                "message": {
                    "code": None,
                    "message": "compiler note",
                    "level": "note",
                    "spans": [],
                },
            },
        ),
    ],
    ids=["empty", "blank", "cached-artifact", "build-script", "nullable-note"],
)
def test_legitimate_no_lint_output_is_not_corruption(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: str,
    output: str,
) -> None:
    """Cargo's valid no-work and informational rows retain clean behavior."""
    result = _run(
        clippy_plugin,
        monkeypatch,
        tmp_path,
        method,
        [(0, output)] * 3,
    )
    assert_that(result.success).is_true()
    assert_that(result.parse_failures_count or 0).is_zero()
    assert_that(result.issues).is_empty()
    assert_that(result.output).is_none()
    if method == "fix":
        assert_that(
            (
                result.initial_issues_count,
                result.fixed_issues_count,
                result.remaining_issues_count,
            ),
        ).is_equal_to((0, 0, 0))


@pytest.mark.parametrize("method", ["check", "fix"])
def test_compiler_error_survives_mixed_corruption(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: str,
) -> None:
    """Parse failures and valid lint rows cannot conceal an independent failure."""
    output = "\n".join((_VALID, _COMPILER_ERROR, _BROKEN))
    result = _run(clippy_plugin, monkeypatch, tmp_path, method, [(101, output)] * 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues).extracting("message").is_equal_to(["kept lint"])
    assert_that(result.output).contains("cannot find value missing", _BROKEN)
    assert_that(result.initial_issues_count).is_none()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()


@pytest.mark.parametrize(
    ("stage", "expected_initial_count", "expected_issues_count"),
    [(0, None, 0), (1, 1, 1), (2, 1, 1)],
    ids=["initial", "mutation", "verify"],
)
def test_fix_timeout_preserves_only_completed_measurements(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: int,
    expected_initial_count: int | None,
    expected_issues_count: int,
) -> None:
    """A stopped process cannot supply a zero before or after measurement."""
    stages: list[tuple[int, str] | subprocess.TimeoutExpired] = [
        (0, _VALID),
        (0, ""),
        (0, ""),
    ]
    stages[stage] = subprocess.TimeoutExpired("cargo", 120)
    result = _run(clippy_plugin, monkeypatch, tmp_path, "fix", stages)

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.residual_unknown).is_true()
    # initial_issues_count stays unknown when the initial check times out.
    assert_that(result.initial_issues_count).is_equal_to(expected_initial_count)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues_count).is_equal_to(expected_issues_count)


@pytest.mark.parametrize(
    "partial",
    [b"Checking crate\npartial output\xff", "Checking crate\npartial output"],
)
def test_timeout_output_remains_readable(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    partial: str | bytes,
) -> None:
    """The Clippy timeout boundary decodes byte output without a bytes repr."""

    def timeout(**kwargs: object) -> tuple[bool, str]:
        raise subprocess.TimeoutExpired("cargo", 120, output=partial)

    monkeypatch.setattr(
        "lintro.tools.clippy.definition.run_subprocess_with_timeout",
        timeout,
    )
    run = clippy_plugin._run_clippy(["cargo", "clippy"], 120, ".")

    assert_that(run.timed_out).is_true()
    assert_that(run.output).is_equal_to(
        partial.decode(errors="replace") if isinstance(partial, bytes) else partial,
    )


@pytest.mark.parametrize("as_bytes", [False, True], ids=["str", "bytes"])
@pytest.mark.parametrize(
    ("output", "findings", "failures", "errors"),
    [
        (_VALID, 1, 0, 0),
        (_VALID + '\n{"reason":"compiler-message",', 1, 1, 0),
        (_VALID + "\n" + _COMPILER_ERROR, 1, 0, 1),
        ("", 0, 0, 0),
    ],
    ids=["lint", "truncated-tail", "compiler-error", "empty"],
)
def test_timeout_retains_diagnostic_statistics(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    findings: int,
    failures: int,
    errors: int,
    as_bytes: bool,
) -> None:
    """Partial native records supply independent statistics despite timeout."""

    def timeout(**kwargs: object) -> tuple[bool, str]:
        raise subprocess.TimeoutExpired(
            "cargo",
            120,
            output=output.encode() if as_bytes else output,
        )

    monkeypatch.setattr(
        "lintro.tools.clippy.definition.run_subprocess_with_timeout",
        timeout,
    )
    run = clippy_plugin._run_clippy(["cargo", "clippy"], 120, ".")

    assert_that(run.timed_out).is_true()
    assert_that(run.success).is_false()
    assert_that(len(run.issues)).is_equal_to(findings)
    assert_that(run.parse_failures).is_equal_to(failures)
    assert_that(run.compiler_errors).is_equal_to(errors)
    assert_that(run.lint_diagnostics).is_equal_to(findings)
    if errors:
        assert_that(run.execution_failed).is_true()
    if output:
        assert_that(run.output).contains(output)
    else:
        assert_that(run.output).contains("timed out")


@pytest.mark.parametrize("as_bytes", [False, True], ids=["str", "bytes"])
@pytest.mark.parametrize(
    ("phase", "output", "expected_counts"),
    [
        # Expected counts: retained issues, parse failures, initial issues.
        pytest.param("check", _VALID, (1, 0, None), id="check-lint"),
        pytest.param(
            "check",
            _VALID + '\n{"reason":"compiler-message",',
            (1, 1, None),
            id="check-truncated-tail",
        ),
        pytest.param(
            "check",
            _VALID + "\n" + _COMPILER_ERROR,
            (1, 0, None),
            id="check-compiler-error",
        ),
        pytest.param("check", "", (0, 0, None), id="check-empty"),
        pytest.param("initial", _VALID, (1, 0, None), id="initial-lint"),
        pytest.param(
            "initial",
            _VALID + '\n{"reason":"compiler-message",',
            (1, 1, None),
            id="initial-truncated-tail",
        ),
        pytest.param(
            "initial",
            _VALID + "\n" + _COMPILER_ERROR,
            (1, 0, None),
            id="initial-compiler-error",
        ),
        pytest.param("initial", "", (0, 0, None), id="initial-empty"),
        pytest.param("mutation", _VALID, (1, 0, 1), id="mutation-lint"),
        pytest.param(
            "mutation",
            _VALID + '\n{"reason":"compiler-message",',
            (1, 1, 1),
            id="mutation-truncated-tail",
        ),
        pytest.param(
            "mutation",
            _VALID + "\n" + _COMPILER_ERROR,
            (1, 0, 1),
            id="mutation-compiler-error",
        ),
        pytest.param("mutation", "", (1, 0, 1), id="mutation-empty"),
        pytest.param("verify", _VALID, (1, 0, 1), id="verify-lint"),
        pytest.param(
            "verify",
            _VALID + '\n{"reason":"compiler-message",',
            (1, 1, 1),
            id="verify-truncated-tail",
        ),
        pytest.param(
            "verify",
            _VALID + "\n" + _COMPILER_ERROR,
            (1, 0, 1),
            id="verify-compiler-error",
        ),
        pytest.param("verify", "", (1, 0, 1), id="verify-empty"),
    ],
)
def test_timeout_diagnostics_reach_check_and_fix(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
    output: str,
    expected_counts: tuple[int, int, int | None],
    as_bytes: bool,
) -> None:
    """Actual process-boundary timeouts retain evidence and stop incomplete fixes."""
    expected_issues, expected_failures, expected_initial = expected_counts
    stage = {"check": 0, "initial": 0, "mutation": 1, "verify": 2}[phase]
    stages: list[tuple[int, str] | subprocess.TimeoutExpired] = [
        (0, _VALID),
        (0, ""),
        (0, ""),
    ]
    stages = stages[: stage + 1]
    stages[stage] = subprocess.TimeoutExpired(
        "cargo",
        120,
        output=output.encode() if as_bytes else output,
    )
    result = _run(
        clippy_plugin,
        monkeypatch,
        tmp_path,
        "check" if phase == "check" else "fix",
        stages,
    )

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues_count).is_equal_to(expected_issues)
    assert_that(result.parse_failures_count or 0).is_equal_to(expected_failures)
    if _COMPILER_ERROR in output:
        assert_that(result.execution_failed).is_true()
    if output:
        assert_that(result.output).contains(output)
    else:
        assert_that(result.output).contains("timed out")
    if output or stage > 0:
        assert_that(result.issues).extracting("message").is_equal_to(["kept lint"])
    assert_that(result.initial_issues_count).is_equal_to(expected_initial)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.residual_unknown).is_equal_to(phase != "check")
    assert_that((tmp_path / "src" / "main.rs").read_text()).is_equal_to(
        "fn main() {}\n",
    )


@pytest.mark.parametrize("method", ["check", "fix"])
def test_denied_lint_and_cargo_summary_are_a_complete_verdict(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: str,
) -> None:
    """Exit 101, progress and a compile summary alone do not mean native failure."""
    output = "\n".join(
        (
            _VALID.replace('"warning"', '"error"'),
            "    Checking accounting v0.1.0",
            'error: could not compile `accounting` (bin "accounting") '
            "due to 1 previous error",
            "warning: build failed, waiting for other jobs to finish...",
            '{"reason":"build-finished","success":false}',
        ),
    )
    result = _run(clippy_plugin, monkeypatch, tmp_path, method, [(101, output)] * 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).is_none()
    assert_that(result.residual_unknown).is_false()
    if method == "fix":
        assert_that(
            (
                result.initial_issues_count,
                result.fixed_issues_count,
                result.remaining_issues_count,
            ),
        ).is_equal_to((1, 0, 1))


@pytest.mark.parametrize("stage", [1, 2], ids=["mutation", "verify"])
@pytest.mark.parametrize("with_clippy", [False, True], ids=["rustc", "mixed-lints"])
def test_complete_fix_preserves_unrepresented_native_failure(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: int,
    with_clippy: bool,
) -> None:
    """Failed complete stages keep rustc evidence alongside measured Clippy counts."""
    denial = json.dumps(
        {
            "reason": "compiler-message",
            "message": {
                "code": {"code": "unconditional_recursion"},
                "message": f"native denial in stage {stage}",
                "level": "error",
                "spans": [],
            },
        },
    )
    lint = _VALID if with_clippy else ""
    stages = [(0, lint)] * 3
    stages[stage] = (101, "\n".join((lint, denial)))
    result = _run(clippy_plugin, monkeypatch, tmp_path, "fix", stages)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).contains(denial)
    assert_that(
        (
            result.initial_issues_count,
            result.fixed_issues_count,
            result.remaining_issues_count,
        ),
    ).is_equal_to((int(with_clippy), 0, int(with_clippy)))


@pytest.mark.parametrize("status", [0, 101], ids=["warning", "denial"])
def test_check_mixed_lints_retain_only_failed_native_diagnostics(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int,
) -> None:
    """A sibling Clippy issue cannot explain a failed rustc lint verdict."""
    rustc = json.dumps(
        {
            "reason": "compiler-message",
            "message": {
                "code": {"code": "unconditional_recursion"},
                "message": "function cannot return without recursing",
                "level": "error" if status else "warning",
                "spans": [],
            },
        },
    )
    output = "\n".join((_VALID, rustc))
    result = _run(clippy_plugin, monkeypatch, tmp_path, "check", [(status, output)])

    assert_that(result.success).is_equal_to(status == 0)
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.issues).extracting("message").is_equal_to(["kept lint"])
    assert_that(result.output).is_equal_to(output if status else None)
    assert_that(result.residual_unknown).is_false()
    assert_that(result.initial_issues_count).is_none()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
