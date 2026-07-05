"""Unit tests for the warn-level module-size gate (issue #1052)."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

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
