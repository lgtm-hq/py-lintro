"""Offline Cargo controls for Clippy lint verdicts and independent failures."""

from __future__ import annotations

import subprocess  # nosec B404 - fixed offline Cargo commands, shell=False
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.parsers.clippy.clippy_parser import parse_clippy_output_with_stats
from lintro.tools.clippy.definition import ClippyPlugin
from tests.conftest import run_git
from tests.integration._tools import require_tool

pytestmark = [
    # Probe Clippy itself against its minimum, rather than rustc's version:
    # patch releases can ship a Clippy component below the enforced minimum.
    require_tool(
        "cargo",
        version_args=["clippy", "--version"],
        pin="clippy",
        label="cargo clippy",
    ),
]

_FIXABLE = 'fn value() -> i32 { return 7; }\nfn main() { println!("{}", value()); }\n'
_PERSISTENT = '#![warn(clippy::eq_op)]\nfn main() { println!("{}", 1 == 1); }\n'


@pytest.fixture(autouse=True)
def offline_cargo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep native controls offline and permit independent workspace jobs."""
    monkeypatch.setenv("CARGO_NET_OFFLINE", "true")
    monkeypatch.setenv("CARGO_BUILD_JOBS", "2")
    monkeypatch.setenv("CARGO_TERM_COLOR", "never")


def _project(root: Path, source: str) -> Path:
    """Create a dependency-free Cargo package and return its source path."""
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text(
        f'[package]\nname="{root.name}"\nversion="0.1.0"\nedition="2021"\n',
    )
    target = root / "src" / "main.rs"
    target.write_text(source)
    return target


def _cargo(
    root: Path,
    selection: tuple[str, ...] = ("--workspace",),
) -> subprocess.CompletedProcess[str]:
    """Capture the real JSON stdout and plain-text stderr from offline Cargo."""
    return subprocess.run(  # nosec B603 B607 - fixed Cargo argv, shell=False
        [
            "cargo",
            "clippy",
            "--offline",
            *selection,
            "--all-targets",
            "--all-features",
            "--message-format=json",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("as_bytes", [False, True], ids=["str", "bytes"])
def test_native_timeout_keeps_partial_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    as_bytes: bool,
) -> None:
    """Real Cargo records survive the process timeout boundary and a broken tail."""
    root = tmp_path / "timeout_control"
    target = _project(root, _PERSISTENT)
    native = _cargo(root)
    assert_that(native.returncode).is_zero()
    findings, failures, errors = parse_clippy_output_with_stats(native.stdout)
    assert_that(findings).is_not_empty()
    assert_that((failures, errors)).is_equal_to((0, 0))
    partial = native.stdout + '\n{"reason":"compiler-message",'
    original_run = subprocess.run

    def timeout(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Replay Cargo's captured output at the native process boundary."""
        if args[:2] == ["cargo", "clippy"] and "--message-format=json" in args:
            assert_that(args).does_not_contain("--fix")
            raise subprocess.TimeoutExpired(
                args,
                120,
                output=partial.encode() if as_bytes else partial,
                stderr=native.stderr.encode() if as_bytes else native.stderr,
            )
        return original_run(args, **kwargs)

    monkeypatch.setattr("subprocess.run", timeout)
    for method in ("check", "fix"):
        result = getattr(ClippyPlugin(), method)([str(root)], {})
        assert_that(result.skipped).is_false()
        assert_that(result.unavailable).is_false()
        assert_that(result.success).is_false()
        assert_that(result.timed_out).is_true()
        assert_that(result.issues_count).is_equal_to(len(findings))
        assert_that(result.issues).extracting("message").is_equal_to(
            [issue.message for issue in findings],
        )
        assert_that(result.parse_failures_count).is_equal_to(1)
        assert_that(result.output).contains(partial, native.stderr)
        assert_that(result.initial_issues_count).is_none()
        assert_that(result.fixed_issues_count).is_none()
        assert_that(result.remaining_issues_count).is_none()
        assert_that(result.residual_unknown).is_equal_to(method == "fix")
    assert_that(target.read_text()).is_equal_to(_PERSISTENT)


@pytest.mark.parametrize(
    ("source", "status", "clippy_lint"),
    [
        ("#![deny(unused_variables)]\nfn main() { let unused = 1; }\n", 101, False),
        (
            "#![deny(unused_variables)]\n"
            "fn value() -> i32 { let unused = 1; return 7; }\n"
            'fn main() { println!("{}", value()); }\n',
            101,
            True,
        ),
        ("#![deny(clippy::needless_return)]\n" + _FIXABLE, 101, True),
        (
            "#![warn(unused_variables)]\n"
            + _FIXABLE.replace("return 7", "let unused = 1; return 7"),
            0,
            True,
        ),
        (_FIXABLE, 0, True),
        ("fn main() {}\n", 0, False),
    ],
    ids=[
        "rustc-deny",
        "rustc-deny-and-clippy",
        "clippy-deny",
        "rustc-warning-and-clippy",
        "warning",
        "clean",
    ],
)
def test_native_lint_verdicts_allow_fixes(
    tmp_path: Path,
    source: str,
    status: int,
    clippy_lint: bool,
) -> None:
    """Denials retain lint verdicts and native fixes can repair both lint families."""
    root = tmp_path / "lint_control"
    target = _project(root, source)
    run_git(["git", "init", "--quiet"], cwd=root)
    native = _cargo(root)
    assert_that(native.returncode).is_equal_to(status)
    findings, failures, errors = parse_clippy_output_with_stats(native.stdout)
    assert_that((failures, errors)).is_equal_to((0, 0))
    assert_that(bool(findings)).is_equal_to(clippy_lint)

    checked = ClippyPlugin().check([str(root)], {})
    assert_that(checked.skipped).is_false()
    assert_that(checked.success).is_equal_to(status == 0)
    assert_that(checked.execution_failed).is_false()
    assert_that(checked.inspected_files).is_true()
    assert_that(checked.issues_count).is_equal_to(len(findings))
    if status != 0 and "unused_variables" in source:
        assert_that(checked.output).contains("unused_variables")
    else:
        assert_that(checked.output).is_none()

    fixed = ClippyPlugin().fix([str(root)], {})
    assert_that(fixed.success).described_as(str(fixed.output)).is_true()
    assert_that(fixed.execution_failed).is_false()
    assert_that(fixed.residual_unknown).is_false()
    assert_that(fixed.initial_issues_count).is_equal_to(len(findings))
    assert_that(fixed.fixed_issues_count).is_equal_to(len(findings))
    assert_that(fixed.remaining_issues_count).is_zero()
    assert_that(fixed.output).is_none()
    assert_that(target.read_text()).does_not_contain("return 7", "let unused")


@pytest.mark.parametrize("with_clippy", [False, True], ids=["rustc", "mixed-lints"])
def test_native_unfixable_rustc_denial_keeps_diagnostics(
    tmp_path: Path,
    with_clippy: bool,
) -> None:
    """Complete check/fix verdicts retain rustc denials beside Clippy findings."""
    source = (
        "#![deny(unconditional_recursion)]\n"
        "#![warn(clippy::eq_op)]\n"
        "fn recurse() { recurse(); }\nfn main() { "
        + ('println!("{}", 1 == 1); ' if with_clippy else "")
        + "recurse(); }\n"
    )
    root = tmp_path / "rustc_denial"
    target = _project(root, source)
    run_git(["git", "init", "--quiet"], cwd=root)
    native = _cargo(root)
    assert_that(native.returncode).is_equal_to(101)
    findings, failures, errors = parse_clippy_output_with_stats(native.stdout)
    assert_that((failures, errors)).is_equal_to((0, 0))
    assert_that(bool(findings)).is_equal_to(with_clippy)

    for method in ("check", "fix"):
        result = getattr(ClippyPlugin(), method)([str(root)], {})
        assert_that(result.skipped).is_false()
        assert_that(result.success).is_false()
        assert_that(result.execution_failed).is_false()
        assert_that(result.inspected_files).is_true()
        assert_that(result.residual_unknown).is_false()
        assert_that(result.issues_count).is_equal_to(len(findings))
        assert_that(result.parse_failures_count).is_none()
        assert_that(result.issues).extracting("code").is_equal_to(
            [issue.code for issue in findings],
        )
        assert_that(result.output).contains("unconditional_recursion", "recurse")
        if method == "fix":
            assert_that(
                (
                    result.initial_issues_count,
                    result.fixed_issues_count,
                    result.remaining_issues_count,
                ),
            ).is_equal_to((len(findings), 0, len(findings)))
    assert_that(target.read_text()).is_equal_to(source)


@pytest.mark.parametrize(
    ("source", "evidence"),
    [
        ("fn main() { missing(); }\n", "E0425"),
        (
            'compile_error!("native uncoded failure");\nfn main() {}\n',
            "native uncoded failure",
        ),
    ],
    ids=["coded-error", "null-code-error"],
)
def test_native_compiler_failures_remain_unmeasured(
    tmp_path: Path,
    source: str,
    evidence: str,
) -> None:
    """Real coded and null-code compiler errors stop before mutation."""
    root = tmp_path / "compiler_control"
    target = _project(root, source)
    native = _cargo(root)
    assert_that(native.returncode).is_equal_to(101)
    findings, failures, errors = parse_clippy_output_with_stats(native.stdout)
    assert_that(findings).is_empty()
    assert_that(failures).is_zero()
    assert_that(errors).is_positive()

    for method in ("check", "fix"):
        result = getattr(ClippyPlugin(), method)([str(root)], {})
        assert_that(result.execution_failed).is_true()
        assert_that(result.success).is_false()
        assert_that(result.output).contains(evidence)
        assert_that(result.inspected_files).is_none()
        assert_that(result.initial_issues_count).is_none()
        assert_that(result.fixed_issues_count).is_none()
        assert_that(result.remaining_issues_count).is_none()
        assert_that(result.residual_unknown).is_equal_to(method == "fix")
    assert_that(target.read_text()).is_equal_to(source)


@pytest.mark.parametrize("failure", ["compiler", "build-script"])
def test_native_failure_preserves_sibling_lints(tmp_path: Path, failure: str) -> None:
    """A workspace warning cannot hide another member's independent failure."""
    root = tmp_path / "mixed_workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers=["warning", "broken"]\nresolver="2"\n',
    )
    warning = _project(root / "warning", _PERSISTENT)
    _project(
        root / "broken",
        "fn main() { missing(); }\n" if failure == "compiler" else "fn main() {}\n",
    )
    (root / "broken" / "build.rs").write_text(
        "fn main() { std::thread::sleep(std::time::Duration::from_secs(2)); "
        + (
            'panic!("build script probe failure"); '
            if failure == "build-script"
            else ""
        )
        + "}\n",
    )
    run_git(["git", "init", "--quiet"], cwd=root)
    # Cache the warning crate so Cargo replays its diagnostics even when the
    # independent failing build script is scheduled first on a busy runner.
    assert_that(_cargo(root, ("-p", "warning")).returncode).is_zero()
    native = _cargo(root)
    assert_that(native.returncode).is_equal_to(101)
    evidence = (
        "E0425" if failure == "compiler" else "failed to run custom build command"
    )
    assert_that(native.stdout + native.stderr).contains(evidence)
    findings, failures, errors = parse_clippy_output_with_stats(native.stdout)
    assert_that(findings).is_not_empty()
    assert_that(failures).is_zero()
    assert_that(errors > 0).is_equal_to(failure == "compiler")

    for method in ("check", "fix"):
        # Re-run the delay: a cached build script lets compiler failure race
        # ahead of the sibling warning's replay on subsequent invocations.
        (root / "broken" / "build.rs").touch()
        result = getattr(ClippyPlugin(), method)([str(root)], {})
        assert_that(result.success).is_false()
        assert_that(result.execution_failed).is_true()
        assert_that(result.issues_count).is_positive()
        assert_that(result.output).contains(evidence)
        if failure == "build-script":
            assert_that(result.output).contains("build script probe failure")
        assert_that(result.inspected_files).is_none()
        assert_that(result.initial_issues_count).is_none()
        assert_that(result.fixed_issues_count).is_none()
        assert_that(result.remaining_issues_count).is_none()
        assert_that(result.residual_unknown).is_equal_to(method == "fix")
    assert_that(warning.read_text()).is_equal_to(_PERSISTENT)
