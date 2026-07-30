"""Tests for scripts/ci/update-security-support.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "ci" / "update-security-support.py"

# A minimal root-style support table (emoji marks).
_ROOT_TABLE = (
    "# Security Policy\n"
    "\n"
    "## Supported Versions\n"
    "\n"
    "| Version | Supported |\n"
    "| ------- | --------- |\n"
    "| 0.80.x  | ✅        |\n"
    "| < 0.80  | ❌        |\n"
    "\n"
    "## Reporting\n"
    "\n"
    "Report to 0.80 maintainers, but this 0.80.x prose must not change.\n"
)

# A minimal .github-style support table (GitHub shortcode marks).
_GITHUB_TABLE = (
    "# Security Policy\n"
    "\n"
    "## Supported Versions\n"
    "\n"
    "| Version | Supported          |\n"
    "| ------- | ------------------ |\n"
    "| 0.64.x  | :white_check_mark: |\n"
    "| < 0.64  | :x:                |\n"
    "\n"
    "## Reporting a Vulnerability\n"
)


def _load_module() -> ModuleType:
    """Load update-security-support.py as an importable test module.

    Returns:
        ModuleType: The loaded module.

    Raises:
        RuntimeError: If the module spec or loader cannot be resolved.
    """
    spec = importlib.util.spec_from_file_location(
        "update_security_support",
        _SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    """Provide the loaded update-security-support module.

    Returns:
        ModuleType: The loaded module.
    """
    return _load_module()


def _support_rows(text: str) -> list[str]:
    """Return the table rows carrying a support mark.

    Args:
        text: A SECURITY.md document.

    Returns:
        list[str]: Rows containing a supported/unsupported mark.
    """
    marks = ("✅", "❌", ":white_check_mark:", ":x:")
    return [
        line
        for line in text.splitlines()
        if "|" in line and any(mark in line for mark in marks)
    ]


def test_parse_major_minor(module: ModuleType) -> None:
    """Major/minor components are extracted from a semver-like version."""
    assert_that(module.parse_major_minor("0.81.0")).is_equal_to((0, 81))
    assert_that(module.parse_major_minor("1.2.3")).is_equal_to((1, 2))
    assert_that(module.parse_major_minor("0.81.0rc1")).is_equal_to((0, 81))


def test_parse_major_minor_rejects_garbage(module: ModuleType) -> None:
    """An unrecognizable version string is rejected."""
    assert_that(module.parse_major_minor).raises(ValueError).when_called_with("nope")


def test_minor_bump_rewrites_root_table(module: ModuleType) -> None:
    """A minor bump rewrites both support rows and preserves emoji marks."""
    result = module.update_security_support(_ROOT_TABLE, major=0, minor=81)

    rows = _support_rows(result)
    assert_that(rows[0]).contains("0.81.x").contains("✅")
    assert_that(rows[1]).contains("< 0.81").contains("❌")
    # Stale line is gone; unrelated prose is untouched.
    assert_that(result).does_not_contain("0.80.x  | ✅")
    assert_that(result).contains(
        "Report to 0.80 maintainers, but this 0.80.x prose must not change.",
    )


def test_minor_bump_preserves_table_alignment(module: ModuleType) -> None:
    """Rewritten rows keep the original pipe alignment (equal-width bump)."""
    result = module.update_security_support(_ROOT_TABLE, major=0, minor=81)

    assert_that(result).contains("| 0.81.x  | ✅        |")
    assert_that(result).contains("| < 0.81  | ❌        |")


def test_minor_bump_rewrites_github_shortcode_table(module: ModuleType) -> None:
    """The .github shortcode-mark table is rewritten and marks are preserved."""
    result = module.update_security_support(_GITHUB_TABLE, major=0, minor=81)

    assert_that(result).contains("| 0.81.x  | :white_check_mark: |")
    assert_that(result).contains("| < 0.81  | :x:                |")
    assert_that(result).does_not_contain("0.64")


def test_patch_bump_is_a_no_op(module: ModuleType) -> None:
    """A patch bump keeps the current major.minor line, so the file is unchanged."""
    # 0.80.x table stamped for the 0.80.5 -> 0.80.6 patch release.
    result = module.update_security_support(_ROOT_TABLE, major=0, minor=80)

    assert_that(result).is_equal_to(_ROOT_TABLE)


def test_rewrite_is_idempotent(module: ModuleType) -> None:
    """Stamping twice yields the same output as stamping once."""
    once = module.update_security_support(_ROOT_TABLE, major=0, minor=81)
    twice = module.update_security_support(once, major=0, minor=81)

    assert_that(twice).is_equal_to(once)


def test_rows_outside_section_are_untouched(module: ModuleType) -> None:
    """Version-like rows outside the Supported Versions section are not rewritten."""
    doc = (
        "## Supported Versions\n"
        "\n"
        "| Version | Supported |\n"
        "| ------- | --------- |\n"
        "| 0.80.x  | ✅        |\n"
        "| < 0.80  | ❌        |\n"
        "\n"
        "## Other Table\n"
        "\n"
        "| Version | Note |\n"
        "| ------- | ---- |\n"
        "| 0.80.x  | keep |\n"
    )
    result = module.update_security_support(doc, major=0, minor=81)

    # In-section row rewritten...
    assert_that(result).contains("| 0.81.x  | ✅        |")
    # ...out-of-section row preserved verbatim.
    assert_that(result).contains("| 0.80.x  | keep |")


def test_main_stamps_both_files(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI stamps root and .github SECURITY.md when both exist."""
    repo = tmp_path
    root_security = repo / "SECURITY.md"
    root_security.write_text(_ROOT_TABLE, encoding="utf-8")
    github_dir = repo / ".github"
    github_dir.mkdir()
    github_security = github_dir / "SECURITY.md"
    github_security.write_text(_GITHUB_TABLE, encoding="utf-8")

    # ``main`` derives the repo root from its own file location; redirect the
    # file discovery to the temp tree instead.
    monkeypatch.setattr(
        module,
        "_security_files",
        lambda root: [root_security, github_security],
    )

    # Version supplied via argv so no pyproject.toml is required.
    exit_code = module.main(["0.81.0"])

    assert_that(exit_code).is_equal_to(0)
    assert_that(root_security.read_text(encoding="utf-8")).contains(
        "| 0.81.x  | ✅        |",
    )
    assert_that(github_security.read_text(encoding="utf-8")).contains(
        "| 0.81.x  | :white_check_mark: |",
    )


def test_main_reads_version_from_pyproject(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` resolves the version from ``RELEASE_VERSION`` when no argv."""
    root_security = tmp_path / "SECURITY.md"
    root_security.write_text(_ROOT_TABLE, encoding="utf-8")

    monkeypatch.setattr(module, "_security_files", lambda root: [root_security])
    monkeypatch.setenv("RELEASE_VERSION", "0.81.0")

    assert_that(module.main([])).is_equal_to(0)
    assert_that(root_security.read_text(encoding="utf-8")).contains(
        "| 0.81.x  | ✅        |",
    )


def test_main_no_security_file_is_noop(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repository without any SECURITY.md is a non-fatal skip."""
    monkeypatch.setattr(module, "_security_files", lambda root: [])

    assert_that(module.main(["0.81.0"])).is_equal_to(0)


def test_main_rejects_invalid_version(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` returns a non-zero exit code for an invalid version string."""
    monkeypatch.setenv("RELEASE_VERSION", "not-a-version")

    assert_that(module.main([])).is_equal_to(2)
