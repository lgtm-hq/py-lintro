"""Offline Cargo controls for Clippy lint verdicts and independent failures."""

from __future__ import annotations

import subprocess  # nosec B404 - fixed offline Cargo commands, shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.clippy.clippy_parser import parse_clippy_output_with_stats
from lintro.tools.clippy.definition import ClippyPlugin
from tests.conftest import run_git
from tests.integration._tools import NO_MIN_VERSION, require_tool

pytestmark = [
    require_tool("rustc", pin="clippy"),
    require_tool(
        "cargo",
        version_args=["clippy", "--version"],
        min_version=NO_MIN_VERSION,
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
        (_FIXABLE, 0, True),
        ("fn main() {}\n", 0, False),
    ],
    ids=["rustc-deny", "rustc-deny-and-clippy", "clippy-deny", "warning", "clean"],
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
    if status != 0 and not clippy_lint:
        assert_that(checked.output).contains("unused_variables")

    fixed = ClippyPlugin().fix([str(root)], {})
    assert_that(fixed.success).described_as(str(fixed.output)).is_true()
    assert_that(fixed.execution_failed).is_false()
    assert_that(fixed.residual_unknown).is_false()
    assert_that(fixed.initial_issues_count).is_equal_to(len(findings))
    assert_that(fixed.fixed_issues_count).is_equal_to(len(findings))
    assert_that(fixed.remaining_issues_count).is_zero()
    assert_that(target.read_text()).does_not_contain("return 7", "let unused")


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


def test_native_build_script_failure_preserves_sibling_lints(tmp_path: Path) -> None:
    """A workspace warning cannot hide another member's plain-text Cargo failure."""
    root = tmp_path / "mixed_workspace"
    root.mkdir()
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers=["warning", "broken"]\nresolver="2"\n',
    )
    warning = _project(root / "warning", _PERSISTENT)
    _project(root / "broken", "fn main() {}\n")
    (root / "broken" / "build.rs").write_text(
        "fn main() { std::thread::sleep(std::time::Duration::from_secs(2)); "
        'panic!("build script probe failure"); }\n',
    )
    run_git(["git", "init", "--quiet"], cwd=root)
    # Cache the warning crate so Cargo replays its diagnostics even when the
    # independent failing build script is scheduled first on a busy runner.
    assert_that(_cargo(root, ("-p", "warning")).returncode).is_zero()
    native = _cargo(root)
    assert_that(native.returncode).is_equal_to(101)
    assert_that(native.stderr).contains("failed to run custom build command")
    findings, failures, errors = parse_clippy_output_with_stats(native.stdout)
    assert_that(findings).is_not_empty()
    assert_that((failures, errors)).is_equal_to((0, 0))

    for method in ("check", "fix"):
        result = getattr(ClippyPlugin(), method)([str(root)], {})
        assert_that(result.success).is_false()
        assert_that(result.execution_failed).is_true()
        assert_that(result.issues_count).is_positive()
        assert_that(result.output).contains(
            "failed to run custom build command",
            "build script probe failure",
        )
        assert_that(result.inspected_files).is_none()
        assert_that(result.initial_issues_count).is_none()
        assert_that(result.fixed_issues_count).is_none()
        assert_that(result.remaining_issues_count).is_none()
        assert_that(result.residual_unknown).is_equal_to(method == "fix")
    assert_that(warning.read_text()).is_equal_to(_PERSISTENT)
