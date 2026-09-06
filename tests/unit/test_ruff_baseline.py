"""Ratchet gate for the ruff structural baseline (issue #2291).

``pyproject.toml`` switches on the ruff families that measure shape — ``C90``
(cyclomatic complexity), ``PLR0912`` (branches), ``PLR0913`` (arguments) and
``PLR0915`` (statements) — with today's violators recorded as per-file ignores
under the ``# --- structural baseline ---`` comment block.

That list is a burn-down list, not a policy. It may only shrink: entries are
deleted as the owning refactor issues (#2311, #2313, #1972, #1995) land. The
``BASELINE`` mapping below mirrors the config exactly, and ``BASELINE_MAX_*``
records how large it was allowed to be, so neither a new suppression nor the
return of a retired one can pass unnoticed.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from assertpy import assert_that

#: Codes whose per-file ignores form the structural baseline.
STRUCTURAL_CODES: frozenset[str] = frozenset(
    {
        "C901",
        "PLR0912",
        "PLR0913",
        "PLR0915",
    },
)

#: The exact structural suppressions recorded when the families were switched
#: on. This freezes identities, not just a count: a pull request may delete a
#: path or a code from this mapping *and* from ``pyproject.toml``, but adding
#: either to the config without a matching entry here fails the ratchet.
BASELINE: dict[str, tuple[str, ...]] = {
    "lintro/ai/apply.py": ("C901", "PLR0912"),
    "lintro/ai/display/status.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/ai/doctor_checks.py": ("PLR0912",),
    "lintro/ai/fallback.py": ("PLR0913",),
    "lintro/ai/fix.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/ai/fix_context.py": ("PLR0913",),
    "lintro/ai/interactive.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/ai/invoke.py": ("PLR0913",),
    "lintro/ai/orchestrator.py": ("PLR0912",),
    "lintro/ai/pipeline.py": ("C901", "PLR0912", "PLR0913"),
    "lintro/ai/providers/base.py": ("PLR0913",),
    "lintro/ai/providers/openai.py": ("PLR0912",),
    "lintro/ai/refinement.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/ai/review/chunker/grouping.py": ("C901",),
    "lintro/ai/review/chunker/shell_run_parse.py": ("C901", "PLR0912"),
    "lintro/ai/review/context/pr_metadata.py": ("PLR0912",),
    "lintro/ai/review/coverage.py": ("PLR0913",),
    "lintro/ai/review/custom_agent_runner.py": ("PLR0913",),
    "lintro/ai/review/finding_matcher.py": ("PLR0915",),
    "lintro/ai/review/github.py": ("PLR0913",),
    "lintro/ai/review/github_lifecycle.py": ("PLR0913",),
    "lintro/ai/review/github_sticky.py": ("PLR0913",),
    "lintro/ai/review/models/finding_record.py": ("C901", "PLR0912"),
    "lintro/ai/review/orchestrator.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/ai/review/synthesis.py": ("PLR0913",),
    "lintro/ai/summary.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/api/core.py": ("PLR0913",),
    "lintro/api/pipeline.py": ("PLR0913",),
    "lintro/cli_utils/commands/check.py": ("PLR0913",),
    "lintro/cli_utils/commands/config.py": ("PLR0915",),
    "lintro/cli_utils/commands/deps.py": ("PLR0912",),
    "lintro/cli_utils/commands/doctor.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/cli_utils/commands/format.py": ("PLR0913",),
    "lintro/cli_utils/commands/install.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/cli_utils/commands/list_tools.py": ("PLR0915",),
    "lintro/cli_utils/commands/test.py": ("PLR0913",),
    "lintro/cli_utils/commands/versions.py": ("PLR0912", "PLR0915"),
    "lintro/config/config_loader.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/config/config_validator.py": ("C901", "PLR0912"),
    "lintro/formatters/styles/github.py": ("PLR0912",),
    "lintro/licenses/ecosystems/npm.py": ("C901", "PLR0912"),
    "lintro/parsers/black/black_parser.py": ("PLR0912",),
    "lintro/parsers/osv_scanner/osv_scanner_parser.py": ("C901", "PLR0912"),
    "lintro/parsers/pytest/format_parsers.py": ("PLR0912", "PLR0915"),
    "lintro/tools/core/update_channels.py": ("PLR0912",),
    "lintro/tools/core/version_parsing.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/tools/definitions/_ts_checker_execution.py": ("PLR0912", "PLR0915"),
    "lintro/tools/definitions/astro_check.py": ("PLR0912",),
    "lintro/tools/definitions/bandit.py": ("C901", "PLR0912", "PLR0913"),
    "lintro/tools/definitions/buf.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/tools/definitions/mypy.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/tools/definitions/oxlint.py": ("PLR0913",),
    "lintro/tools/definitions/pytest.py": ("C901", "PLR0912"),
    "lintro/tools/definitions/ruff.py": ("PLR0913",),
    "lintro/tools/definitions/spectral.py": ("PLR0912",),
    "lintro/tools/definitions/stylelint.py": ("PLR0912",),
    "lintro/tools/definitions/yamllint.py": ("PLR0912", "PLR0915"),
    "lintro/tools/implementations/pytest/collection.py": ("PLR0912",),
    "lintro/tools/implementations/pytest/coverage_processor.py": ("C901", "PLR0912"),
    "lintro/tools/implementations/pytest/output_parsers.py": (
        "C901",
        "PLR0912",
        "PLR0915",
    ),
    "lintro/tools/implementations/pytest/pytest_command_builder.py": ("PLR0912",),
    "lintro/tools/implementations/pytest/pytest_option_validators.py": (
        "C901",
        "PLR0912",
        "PLR0913",
        "PLR0915",
    ),
    "lintro/tools/implementations/ruff/check.py": ("PLR0915",),
    "lintro/tools/implementations/ruff/commands.py": ("PLR0912",),
    "lintro/tools/implementations/ruff/fix.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/utils/console/logger.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/utils/console/pre_execution_summary.py": ("PLR0912",),
    "lintro/utils/display_helpers.py": ("PLR0912",),
    "lintro/utils/doctor_report.py": ("PLR0912",),
    "lintro/utils/environment/collectors.py": ("PLR0912",),
    "lintro/utils/execution/parallel_executor.py": ("PLR0913",),
    "lintro/utils/execution/run_renderer.py": ("PLR0912", "PLR0913"),
    "lintro/utils/execution/tool_configuration.py": (
        "C901",
        "PLR0912",
        "PLR0913",
        "PLR0915",
    ),
    "lintro/utils/jsonc.py": ("PLR0912",),
    "lintro/utils/native_parsers.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/utils/output/file_writer.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/utils/output/sarif/document.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/utils/path_filtering.py": ("C901", "PLR0912"),
    "lintro/utils/post_checks.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/utils/project_detection.py": ("C901", "PLR0912", "PLR0915"),
    "lintro/utils/result_formatters.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/utils/summary_tables.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/utils/tool_executor.py": ("C901", "PLR0912", "PLR0913", "PLR0915"),
    "lintro/utils/tsconfig.py": ("PLR0912",),
    "lintro/watch/watcher.py": ("PLR0913",),
    "scripts/ci/format-changelog.py": ("C901", "PLR0912", "PLR0915"),
    "scripts/ci/format-security-comment.py": ("C901", "PLR0912", "PLR0915"),
    "scripts/ci/site/migrate-docs-content.py": ("C901", "PLR0912", "PLR0915"),
    "scripts/ci/verify-manifest-tools.py": ("C901", "PLR0912", "PLR0915"),
    "tests/unit/ai/review/test_cross_chunk_synthesis_2269.py": ("PLR0913",),
    "tests/unit/cli_utils/commands/test_doctor_command.py": ("PLR0913",),
    "tests/unit/tools/conftest.py": ("PLR0913",),
    "tests/unit/watch/test_runner.py": ("PLR0913",),
    "tools/ascii_resizer/cli.py": ("PLR0913",),
}

#: Ceilings on ``BASELINE`` itself, recorded when the families were switched on
#: and deliberately *not* derived from ``BASELINE``. Without them a pull request
#: could edit the mapping and ``pyproject.toml`` together and reintroduce a
#: suppression that a previous ratchet step removed. Both may only go *down*,
#: and both are pinned to the exact live counts (issue #1739): any slack here
#: is room for exactly that reintroduction.
BASELINE_MAX_FILES: int = 94
BASELINE_MAX_SUPPRESSIONS: int = 182

#: Ceilings, not current values: the configured thresholds may be lowered
#: without touching these constants (ratchet plan for complexity: 15 -> 12 ->
#: 10), but never raised above them.
MAX_ALLOWED_COMPLEXITY: int = 15
MAX_ALLOWED_ARGS: int = 8
MAX_ALLOWED_BRANCHES: int = 12
MAX_ALLOWED_STATEMENTS: int = 50


def _load_ruff_lint_config() -> dict[str, Any]:
    """Load the ``[tool.ruff.lint]`` table from the repository ``pyproject.toml``.

    Returns:
        dict[str, Any]: The parsed ``[tool.ruff.lint]`` configuration table.
    """
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    parsed = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", parsed["tool"]["ruff"]["lint"])


def _structural_baseline_entries() -> dict[str, list[str]]:
    """Collect per-file ignores that suppress at least one structural code.

    Returns:
        dict[str, list[str]]: Mapping of file pattern to its ignored codes,
            restricted to entries carrying a structural code.
    """
    per_file_ignores: dict[str, list[str]] = _load_ruff_lint_config()[
        "per-file-ignores"
    ]
    return {
        pattern: list(codes)
        for pattern, codes in per_file_ignores.items()
        if STRUCTURAL_CODES.intersection(codes)
    }


def _pairs(entries: Mapping[str, Sequence[str]]) -> set[tuple[str, str]]:
    """Flatten a pattern-to-codes mapping into structural ``(path, code)`` pairs.

    Args:
        entries: Mapping of file pattern to the rule codes ignored for it.

    Returns:
        set[tuple[str, str]]: One pair per structural suppression.
    """
    return {
        (pattern, code)
        for pattern, codes in entries.items()
        for code in codes
        if code in STRUCTURAL_CODES
    }


def test_structural_families_are_selected() -> None:
    """The structural rule families stay enabled in the ruff selection."""
    select = _load_ruff_lint_config()["select"]

    assert_that(select).contains("C90", "PLR0912", "PLR0913", "PLR0915", "RUF100")


def test_structural_thresholds_are_not_raised() -> None:
    """Complexity, argument and size thresholds stay at their recorded values."""
    lint_config = _load_ruff_lint_config()
    mccabe: dict[str, int] = lint_config["mccabe"]
    pylint: dict[str, int] = lint_config["pylint"]

    assert_that(mccabe["max-complexity"]).is_less_than_or_equal_to(
        MAX_ALLOWED_COMPLEXITY,
    )
    assert_that(pylint["max-args"]).is_less_than_or_equal_to(MAX_ALLOWED_ARGS)
    assert_that(pylint["max-branches"]).is_less_than_or_equal_to(
        MAX_ALLOWED_BRANCHES,
    )
    assert_that(pylint["max-statements"]).is_less_than_or_equal_to(
        MAX_ALLOWED_STATEMENTS,
    )


def test_structural_baseline_matches_pyproject_exactly() -> None:
    """``BASELINE`` and the config agree on every ``(path, code)`` suppression."""
    configured = _pairs(_structural_baseline_entries())
    frozen = _pairs(BASELINE)

    assert_that(
        sorted(f"{pattern}:{code}" for pattern, code in configured - frozen),
    ).described_as(
        "structural baseline may only shrink: delete per-file-ignores entries "
        "for C901/PLR0912/PLR0913/PLR0915, never add them",
    ).is_empty()
    assert_that(
        sorted(f"{pattern}:{code}" for pattern, code in frozen - configured),
    ).described_as(
        "prune BASELINE in step with pyproject.toml: a suppression left here "
        "after the config drops it can be reintroduced unnoticed",
    ).is_empty()


def test_structural_baseline_may_only_shrink() -> None:
    """``BASELINE`` itself never grows past the sizes recorded for the ratchet."""
    assert_that(len(BASELINE)).described_as(
        "BASELINE_MAX_FILES may only be lowered, never raised",
    ).is_less_than_or_equal_to(BASELINE_MAX_FILES)
    assert_that(len(_pairs(BASELINE))).described_as(
        "BASELINE_MAX_SUPPRESSIONS may only be lowered, never raised",
    ).is_less_than_or_equal_to(BASELINE_MAX_SUPPRESSIONS)


def test_baseline_entries_reference_existing_files() -> None:
    """Every baselined path still exists, so stale entries cannot linger."""
    repo_root = Path(__file__).resolve().parents[2]

    missing = [
        pattern
        for pattern in _structural_baseline_entries()
        if not (repo_root / pattern).is_file()
    ]

    assert_that(missing).described_as(
        "delete structural baseline entries for files that no longer exist",
    ).is_empty()
