"""Unit tests for the warn-level module-size gate (issue #1052)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils import gates
from lintro.utils.console import ThreadSafeConsoleLogger
from lintro.utils.module_size import (
    DEFAULT_MODULE_SIZE_BASELINE,
    DEFAULT_MODULE_SIZE_EXCLUDES,
    DEFAULT_MODULE_SIZE_THRESHOLD,
    OversizedModule,
    count_module_lines,
    find_oversized_modules,
    resolve_module_size_settings,
)


def _write_module(
    *,
    directory: Path,
    name: str,
    line_count: int,
) -> Path:
    """Create a Python module with a given number of lines.

    Args:
        directory: Directory in which to create the module.
        name: File name for the module.
        line_count: Number of lines to write.

    Returns:
        Path: Path to the created module.
    """
    path = directory / name
    path.write_text(
        "\n".join(f"x = {index}" for index in range(line_count)) + "\n",
        encoding="utf-8",
    )
    return path


def test_count_module_lines_counts_physical_lines(
    *,
    tmp_path: Path,
) -> None:
    """A module's physical line count is measured accurately.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    module = _write_module(directory=tmp_path, name="counted.py", line_count=42)

    assert_that(count_module_lines(file_path=str(module))).is_equal_to(42)


@pytest.mark.parametrize(
    ("content", "expected"),
    [("", 0), ("x = 1", 1), ("# café\nx = 1\n", 2)],
)
def test_count_module_lines_handles_valid_text(
    tmp_path: Path,
    content: str,
    expected: int,
) -> None:
    """Empty files, unterminated lines and valid UTF-8 are measured.

    Args:
        tmp_path: Pytest temporary directory fixture.
        content: Module contents to measure.
        expected: Expected physical line count.
    """
    module = tmp_path / "valid.py"
    module.write_text(content, encoding="utf-8")

    assert_that(count_module_lines(file_path=str(module))).is_equal_to(expected)


@pytest.mark.parametrize(
    "read_error",
    [PermissionError("permission denied"), OSError("disk read failed")],
    ids=["permission", "io"],
)
@pytest.mark.parametrize("output_format", ["plain", "json"])
def test_unreadable_module_fails_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_error: OSError,
    output_format: str,
) -> None:
    """Read failures abort the gate with the file and reason in all modes.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
        read_error: Filesystem failure raised while reading a discovered module.
        output_format: Console or machine-readable output mode.
    """
    module = _write_module(directory=tmp_path, name="unreadable.py", line_count=2)
    monkeypatch.setattr(gates, "load_module_size_config", lambda: {})

    with (
        patch("lintro.utils.module_size.open", side_effect=read_error),
        pytest.raises(OSError) as raised,
    ):
        gates.execute_gates(
            action=Action.CHECK,
            paths=[str(tmp_path)],
            exclude=None,
            include_venv=False,
            output_format=output_format,
            logger=ThreadSafeConsoleLogger(),
            all_results=[],
            total_issues=0,
        )

    assert_that(str(raised.value)).contains(str(module), str(read_error))


def test_invalid_utf8_module_reports_decoding_failure(tmp_path: Path) -> None:
    """Invalid UTF-8 cannot be counted as a small, readable module.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    module = tmp_path / "invalid.py"
    module.write_bytes(b"# valid first line\n\xff\n")

    with pytest.raises(OSError) as raised:
        find_oversized_modules(paths=[str(tmp_path)], baseline=())

    assert_that(str(raised.value)).contains(str(module), "utf-8", "invalid start byte")


def test_count_module_lines_reports_missing_file(tmp_path: Path) -> None:
    """Direct callers receive an error when a module disappears.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    missing = tmp_path / "missing.py"

    with pytest.raises(OSError) as raised:
        count_module_lines(file_path=str(missing))

    assert_that(str(raised.value)).contains(str(missing), "No such file")


def test_small_module_produces_no_warning(
    *,
    tmp_path: Path,
) -> None:
    """A module under the threshold yields no violations.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    _write_module(directory=tmp_path, name="small.py", line_count=100)

    violations = find_oversized_modules(
        paths=[str(tmp_path)],
        threshold=800,
        baseline=(),
    )

    assert_that(violations).is_empty()


def test_oversized_module_is_flagged(
    *,
    tmp_path: Path,
) -> None:
    """A module over the threshold and not baselined is flagged.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    _write_module(directory=tmp_path, name="huge.py", line_count=900)

    violations = find_oversized_modules(
        paths=[str(tmp_path)],
        threshold=800,
        baseline=(),
    )

    flagged = [module.path for module in violations]
    assert_that(flagged).is_length(1)
    assert_that(flagged[0]).contains("huge.py")
    assert_that(violations[0]).is_instance_of(OversizedModule)
    assert_that(violations[0].line_count).is_equal_to(900)


def test_baselined_module_is_skipped(
    *,
    tmp_path: Path,
) -> None:
    """A baselined module over the threshold is not flagged.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    package = tmp_path / "pkg"
    package.mkdir()
    _write_module(directory=package, name="legacy.py", line_count=900)

    violations = find_oversized_modules(
        paths=[str(tmp_path)],
        threshold=800,
        baseline=("pkg/legacy.py",),
    )

    assert_that(violations).is_empty()


def test_threshold_is_configurable(
    *,
    tmp_path: Path,
) -> None:
    """Lowering the threshold flags a previously-passing module.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    _write_module(directory=tmp_path, name="medium.py", line_count=500)

    passing = find_oversized_modules(
        paths=[str(tmp_path)],
        threshold=800,
        baseline=(),
    )
    flagged = find_oversized_modules(
        paths=[str(tmp_path)],
        threshold=400,
        baseline=(),
    )

    assert_that(passing).is_empty()
    assert_that(flagged).is_not_empty()
    assert_that(flagged[0].path).contains("medium.py")


def test_excluded_paths_are_skipped(
    *,
    tmp_path: Path,
) -> None:
    """Modules under excluded paths are not scanned.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    samples = tmp_path / "test_samples"
    samples.mkdir()
    _write_module(directory=samples, name="fixture.py", line_count=900)

    violations = find_oversized_modules(
        paths=[str(tmp_path)],
        threshold=800,
        baseline=(),
        exclude_patterns=("test_samples/",),
    )

    assert_that(violations).is_empty()


def test_default_baseline_matches_the_configured_baseline() -> None:
    """The in-code default and the pyproject baseline are the same list.

    ``resolve_module_size_settings`` falls back to
    :data:`DEFAULT_MODULE_SIZE_BASELINE` whenever the configured value is
    missing or malformed, so a repo whose ``pyproject.toml`` baseline had
    drifted from the default would silently grandfather a different set of
    modules on that fallback path. Pinning the two together keeps the gate's
    two sources of truth one source of truth (#2301).
    """
    repo_root = Path(__file__).resolve().parents[3]
    parsed = tomllib.loads(
        (repo_root / "pyproject.toml").read_text(encoding="utf-8"),
    )
    configured = parsed["tool"]["lintro"]["module_size"]["baseline"]

    assert_that(tuple(configured)).is_equal_to(DEFAULT_MODULE_SIZE_BASELINE)


def test_default_threshold_is_eight_hundred() -> None:
    """The default warn-level threshold is 800 lines."""
    assert_that(DEFAULT_MODULE_SIZE_THRESHOLD).is_equal_to(800)


def test_resolve_settings_returns_defaults_for_empty_config() -> None:
    """An empty config resolves to the module defaults."""
    threshold, baseline, exclude = resolve_module_size_settings(config={})

    assert_that(threshold).is_equal_to(DEFAULT_MODULE_SIZE_THRESHOLD)
    assert_that(baseline).is_equal_to(DEFAULT_MODULE_SIZE_BASELINE)
    assert_that(exclude).is_equal_to(DEFAULT_MODULE_SIZE_EXCLUDES)


def test_resolve_settings_parses_valid_overrides() -> None:
    """Valid overrides are coerced into concrete types."""
    threshold, baseline, exclude = resolve_module_size_settings(
        config={
            "threshold": "600",
            "baseline": ["pkg/legacy.py"],
            "exclude": ["custom/"],
        },
    )

    assert_that(threshold).is_equal_to(600)
    assert_that(baseline).is_equal_to(("pkg/legacy.py",))
    assert_that(exclude).is_equal_to(("custom/",))


def test_resolve_settings_falls_back_on_invalid_threshold() -> None:
    """A non-numeric or non-positive threshold falls back to the default."""
    for bad_value in ("not-a-number", -5, 0, True, None):
        threshold, _, _ = resolve_module_size_settings(
            config={"threshold": bad_value},
        )

        assert_that(threshold).is_equal_to(DEFAULT_MODULE_SIZE_THRESHOLD)


def test_resolve_settings_rejects_scalar_sequences() -> None:
    """A scalar string baseline/exclude falls back instead of char-splitting."""
    threshold, baseline, exclude = resolve_module_size_settings(
        config={"baseline": "pkg/legacy.py", "exclude": "custom/"},
    )

    assert_that(threshold).is_equal_to(DEFAULT_MODULE_SIZE_THRESHOLD)
    assert_that(baseline).is_equal_to(DEFAULT_MODULE_SIZE_BASELINE)
    assert_that(exclude).is_equal_to(DEFAULT_MODULE_SIZE_EXCLUDES)
