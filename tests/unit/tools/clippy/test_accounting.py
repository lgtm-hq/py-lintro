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
    ["error: invalid configuration", _COMPILER_ERROR, _VALID + "\n" + _COMPILER_ERROR],
    ids=["config", "compiler", "compiler-and-lint"],
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


@pytest.mark.parametrize("stage", [0, 1, 2], ids=["initial", "mutation", "verify"])
def test_fix_timeout_preserves_only_completed_measurements(
    clippy_plugin: ClippyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: int,
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
    assert_that(result.initial_issues_count).is_equal_to(None if stage == 0 else 1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues_count).is_equal_to(0 if stage == 0 else 1)
