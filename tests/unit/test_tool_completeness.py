"""Parametrized tool-completeness gate over the plugin registry.

This turns the ``lintro-verify`` new-tool checklist into a merge gate
(issue #1509, epic #1508). Every registered tool plugin is asserted to carry
the full set of integration surfaces and to keep its sources of truth in
agreement:

- ``lintro/tools/manifest.json`` entry
- ``DEFAULT_TOOL_PRIORITIES`` drives the *effective* priority (no dead
  ``priority=`` declarations that silently fall back to 50)
- manifest ``tags`` agree with the definition's ``tool_type``
- install hints in ``lintro/tools/core/version_checking.py``
- ``docs/tool-analysis/<tool>-analysis.md`` and a ``docs/configuration.md``
  section
- a README supported-tools table row
- unit coverage under ``tests/unit/tools/<tool>/`` and an integration test
  under ``tests/integration/tools/``
- fixtures under ``test_samples/tools/<lang>/<tool>/``
- an ``apps/site`` mirror entry

Known gaps are captured in the explicit, named exemption lists below. Each
entry carries a rationale and — where a remediation issue is open — a
``TODO(#<issue>)`` marker. The gate is green today and *shrinks* as the linked
issues land; adding a new tool without its surfaces fails immediately unless a
reviewer adds a documented exemption here (absence is a failure, silence is
not).

Follows the meta-gate pattern in ``tests/unit/core/test_version_requirements.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.core.tool_manager import ToolManager
from lintro.tools.core.version_checking import (
    get_install_hints,
    get_minimum_versions,
)
from lintro.utils.unified_config import get_tool_priority

# ── Repository layout ──────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "lintro" / "tools" / "manifest.json"
TOOL_ANALYSIS_DIR = REPO_ROOT / "docs" / "tool-analysis"
CONFIGURATION_DOC = REPO_ROOT / "docs" / "configuration.md"
README = REPO_ROOT / "README.md"
UNIT_TOOLS_DIR = REPO_ROOT / "tests" / "unit" / "tools"
INTEGRATION_TOOLS_DIR = REPO_ROOT / "tests" / "integration" / "tools"
SAMPLES_DIR = REPO_ROOT / "test_samples" / "tools"
SITE_NAV = REPO_ROOT / "apps" / "site" / "src" / "data" / "sidebar-nav.ts"


# ── Registry snapshot (single source of truth for parametrization) ─────────
def _registry_definitions() -> dict[str, object]:
    """Return ``{registry_name: ToolDefinition}`` for every registered plugin.

    Returns:
        Mapping of registry tool name (as ``lintro list-tools`` shows it, e.g.
        ``astro-check``) to its :class:`~lintro.plugins.protocol.ToolDefinition`.
    """
    manager = ToolManager()
    return {name: plugin.definition for name, plugin in manager.get_all_tools().items()}


_DEFINITIONS = _registry_definitions()
TOOL_NAMES: list[str] = sorted(_DEFINITIONS)


def _canonical(name: str) -> str:
    """Return the underscore form used by manifest/tests/sample dirs.

    Args:
        name: Registry tool name (may contain hyphens, e.g. ``astro-check``).

    Returns:
        Underscore-normalized name (e.g. ``astro_check``).
    """
    return name.replace("-", "_")


def _slug(name: str) -> str:
    """Return the hyphenated slug used by docs/site (e.g. ``dotenv-linter``).

    Args:
        name: Registry tool name.

    Returns:
        Hyphenated slug.
    """
    return name.replace("_", "-")


# ── Cross-surface aliases ──────────────────────────────────────────────────
# Some tools are documented under a shared or renamed heading/page/filename.
# Map registry name -> extra strings that also count as "mentions this tool".
_DOC_ALIASES: dict[str, set[str]] = {
    # oxlint + oxfmt share a single "oxc" analysis doc and site page.
    "oxlint": {"oxc"},
    "oxfmt": {"oxc"},
    # tsc is documented/badged as the TypeScript compiler.
    "tsc": {"typescript"},
    # astro-check is badged simply as "Astro" in the README table.
    "astro_check": {"astro"},
}


def _aliases(name: str) -> set[str]:
    """Return every lowercase token that denotes ``name`` across text surfaces.

    Args:
        name: Registry tool name.

    Returns:
        Set of lowercase strings to search for in README/config/doc filenames.
    """
    tokens = {name.lower(), _slug(name).lower(), name.replace("-", " ").lower()}
    tokens |= _DOC_ALIASES.get(name, set())
    tokens |= _DOC_ALIASES.get(_canonical(name), set())
    return tokens


# ── ToolType <-> manifest tag mapping ──────────────────────────────────────
_TYPE_TO_TAG: dict[ToolType, str] = {
    ToolType.LINTER: "linter",
    ToolType.FORMATTER: "formatter",
    ToolType.TYPE_CHECKER: "type_checker",
    ToolType.DOCUMENTATION: "documentation",
    ToolType.SECURITY: "security",
    ToolType.INFRASTRUCTURE: "infrastructure",
    ToolType.TEST_RUNNER: "testing",
}


def _expected_tags(tool_type: ToolType) -> set[str]:
    """Translate a ``ToolType`` bitmask into the manifest tag set it implies.

    Args:
        tool_type: The definition's ``tool_type`` flags.

    Returns:
        Set of manifest tag strings implied by the flags.
    """
    return {tag for flag, tag in _TYPE_TO_TAG.items() if flag in tool_type}


def _load_manifest_tools() -> dict[str, dict[str, object]]:
    """Return ``{name: entry}`` parsed from ``manifest.json``.

    Returns:
        Mapping of manifest tool name to its raw entry dict.
    """
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        t["name"]: t
        for t in data.get("tools", [])
        if isinstance(t, dict) and t.get("name")
    }


_MANIFEST_TOOLS = _load_manifest_tools()


# ────────────────────────────────────────────────────────────────────────────
#  EXEMPTION LISTS
#
#  Each maps a registry tool name to a human-readable rationale. Prefer a
#  TODO(#<issue>) marker when a remediation issue is open so the entry can be
#  deleted the moment the issue lands. These lists should only ever shrink.
# ────────────────────────────────────────────────────────────────────────────

# Binary-less AI plugins have no external tool to version/install and are not
# advertised through the manifest that drives install/version tooling.
MANIFEST_EXEMPT: dict[str, str] = {
    "idiom-review": "AI plugin, no external binary; not a manifest tool (#1496)",
}

# Manifest tags that do not match the tool_type the definition declares.
# dotenv-linter is the tracked example (#1495); the rest omit *secondary* type
# flags (infrastructure/type_checker/documentation) or tag security tools as
# "linter". Documented under the epic-#1490 audit; align manifest + tool_type.
TAGS_EXEMPT: dict[str, str] = {
    "dotenv_linter": "manifest tags [linter,formatter] but tool_type=LINTER — TODO(#1495)",  # noqa: E501
    "actionlint": "manifest omits 'infrastructure' present in tool_type (#1490)",
    "astro-check": "manifest omits 'type_checker' present in tool_type (#1490)",
    "bandit": "manifest tags 'linter' but tool_type=SECURITY only (#1490)",
    "cargo_deny": "manifest tags 'linter', omits 'infrastructure' vs tool_type (#1490)",  # noqa: E501
    "hadolint": "manifest omits 'infrastructure' present in tool_type (#1490)",
    "pydoclint": "manifest omits 'documentation' present in tool_type (#1490)",
    "sqlfluff": "manifest omits 'formatter' present in tool_type (#1490)",
    "svelte-check": "manifest omits 'type_checker' present in tool_type (#1490)",
    "vue-tsc": "manifest omits 'type_checker' present in tool_type (#1490)",
}

# Tools whose definition priority is "dead": DEFAULT_TOOL_PRIORITIES either has
# no entry (falls back to 50) or a different value, so the declared priority is
# never the effective one. pip-audit is the tracked example (#1506); align each
# definition's priority with DEFAULT_TOOL_PRIORITIES.
PRIORITY_EXEMPT: dict[str, str] = {
    "pip_audit": "declares 90 but DEFAULT_TOOL_PRIORITIES lacks entry (eff. 50) — TODO(#1506)",  # noqa: E501
    "actionlint": "declares 40, DEFAULT_TOOL_PRIORITIES says 55 (#1490)",
    "astro-check": "declares 83, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "bandit": "declares 90, DEFAULT_TOOL_PRIORITIES says 45 (#1490)",
    "black": "declares 90, DEFAULT_TOOL_PRIORITIES says 15 (#1490)",
    "cargo_audit": "declares 95, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "cargo_deny": "declares 90, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "clippy": "declares 85, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "commitlint": "declares 35, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "gitleaks": "declares 90, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "idiom-review": "declares 95, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1496)",
    "oxfmt": "declares 80, DEFAULT_TOOL_PRIORITIES says 25 (#1490)",
    "prettier": "declares 80, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "pydoclint": "declares 45, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "pytest": "declares 90, DEFAULT_TOOL_PRIORITIES says 100 (#1490)",
    "ruff": "declares 85, DEFAULT_TOOL_PRIORITIES says 20 (#1490)",
    "rustfmt": "declares 80, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "semgrep": "declares 85, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "svelte-check": "declares 83, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "vue-tsc": "declares 83, no DEFAULT_TOOL_PRIORITIES entry (eff. 50) (#1490)",
    "yamllint": "declares 40, DEFAULT_TOOL_PRIORITIES says 35 (#1490)",
}

# No install hint template in version_checking.py.
INSTALL_HINT_EXEMPT: dict[str, str] = {
    "idiom-review": "AI plugin installed via lintro[ai] extra, not an external binary (#1496)",  # noqa: E501
}

# No docs/tool-analysis/<tool>-analysis.md.
TOOL_ANALYSIS_EXEMPT: dict[str, str] = {
    "idiom-review": "AI plugin; analysis-doc applicability tracked in #1496",
    "cargo_audit": "no tool-analysis doc yet (#1490)",
    "gitleaks": "no tool-analysis doc yet (#1490)",
    "rustfmt": "no tool-analysis doc yet (#1490)",
    "semgrep": "no tool-analysis doc yet (#1490)",
    "shellcheck": "no tool-analysis doc yet (#1490)",
    "shfmt": "no tool-analysis doc yet (#1490)",
    "sqlfluff": "no tool-analysis doc yet (#1490)",
    "taplo": "no tool-analysis doc yet (#1490)",
}

# No dedicated docs/configuration.md section.
CONFIGURATION_EXEMPT: dict[str, str] = {
    "idiom-review": "not in configuration.md; documented under AI features — TODO(#1496)",  # noqa: E501
    "cargo_audit": "no configuration.md section yet (#1490)",
    "rustfmt": "no configuration.md section yet (#1490)",
    "pytest": "only appears in the tool-ordering table, no dedicated section (#1490)",
}

# No README supported-tools table row.
README_EXEMPT: dict[str, str] = {
    "idiom-review": "missing from README supported-tools table — TODO(#1496)",
    "pytest": "test runner is not listed in the supported-tools table (#1490)",
}

# No tests/unit/tools/<tool>/ directory.
UNIT_TESTS_EXEMPT: dict[str, str] = {
    "actionlint": "no tests/unit/tools/actionlint/ directory yet (#1490)",
    "black": "no tests/unit/tools/black/ directory yet (#1490)",
    "clippy": "no tests/unit/tools/clippy/ directory yet (#1490)",
    "markdownlint": "no tests/unit/tools/markdownlint/ directory yet (#1490)",
    "yamllint": "no tests/unit/tools/yamllint/ directory yet (#1490)",
}

# No integration test under tests/integration/tools/.
INTEGRATION_TESTS_EXEMPT: dict[str, str] = {
    "commitlint": "integration test at tests/integration/, not tools/ — TODO(#1497)",
    "actionlint": "integration test at tests/integration/, not tools/ (#1490)",
    "markdownlint": "integration test at tests/integration/, not tools/ (#1490)",
    "pydoclint": "integration test at tests/integration/, not tools/ (#1490)",
    "stylelint": "integration test at tests/integration/, not tools/ (#1490)",
    "pip_audit": "no integration test under tests/integration/tools/ — TODO(#1506)",
    "cargo_audit": "no integration test yet (#1490)",
    "clippy": "no integration test yet (#1490)",
    "hadolint": "no integration test yet (#1490)",
    "idiom-review": "AI plugin; no external-tool integration test (#1496)",
    "pytest": "test runner exercised via the suite itself, no wrapper integration test (#1490)",  # noqa: E501
}

# No test_samples/tools/<lang>/<tool>/ directory named after the tool.
SAMPLES_EXEMPT: dict[str, str] = {
    "actionlint": "samples live at test_samples/tools/config/github_actions/ (#1497)",
    "hadolint": "samples live at test_samples/tools/config/docker/ (#1497)",
    "markdownlint": "samples live at test_samples/tools/config/markdown/ (#1497)",
    "yamllint": "samples live at test_samples/tools/config/yaml/ (#1497)",
    "black": "exercised against shared test_samples/tools/python fixtures (#1490)",
    "idiom-review": "AI plugin reads a git diff, not sample files (#1496)",
}

# No apps/site mirror entry in sidebar-nav.ts.
SITE_MIRROR_EXEMPT: dict[str, str] = {
    "pip_audit": "no apps/site mirror page/data entry — TODO(#1506)",
    "cargo_audit": "no apps/site mirror entry yet (#1490)",
    "commitlint": "no apps/site mirror entry yet (#1490)",
    "dotenv_linter": "no apps/site mirror entry yet (#1490)",
    "gitleaks": "no apps/site mirror entry yet (#1490)",
    "idiom-review": "no apps/site mirror entry yet (#1496)",
    "rustfmt": "no apps/site mirror entry yet (#1490)",
    "semgrep": "no apps/site mirror entry yet (#1490)",
    "shellcheck": "no apps/site mirror entry yet (#1490)",
    "shfmt": "no apps/site mirror entry yet (#1490)",
    "sqlfluff": "no apps/site mirror entry yet (#1490)",
    "stylelint": "no apps/site mirror entry yet (#1490)",
    "taplo": "no apps/site mirror entry yet (#1490)",
    "vale": "no apps/site mirror entry yet (#1490)",
}


# ── Shared helpers ──────────────────────────────────────────────────────────
def _skip_if_exempt(tool: str, exemptions: dict[str, str]) -> None:
    """Skip the current test when ``tool`` has a documented exemption.

    Args:
        tool: Registry tool name.
        exemptions: One of the named exemption dicts above.
    """
    reason = exemptions.get(tool)
    if reason is not None:
        pytest.skip(f"{tool} exempt: {reason}")


def _file_contains_any(path: Path, needles: set[str]) -> bool:
    """Return whether the lowercased file text contains any of ``needles``.

    Args:
        path: File to read.
        needles: Lowercase substrings to look for.

    Returns:
        True if any needle is present, else False.
    """
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8").lower()
    return any(needle in text for needle in needles)


def _heading_mentions(path: Path, needles: set[str]) -> bool:
    """Return whether any Markdown heading line contains one of ``needles``.

    Args:
        path: Markdown file to scan.
        needles: Lowercase substrings to look for in ``##``..``####`` headings.

    Returns:
        True if a heading mentions the tool, else False.
    """
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        # Match genuine ATX Markdown headings only (``#``..``######`` followed
        # by whitespace), never shebangs or ``#``-prefixed comments.
        if not re.match(r"^#{1,6}\s", line.strip()):
            continue
        lowered = line.strip().lower()
        if any(needle in lowered for needle in needles):
            return True
    return False


# ── Baseline sanity ─────────────────────────────────────────────────────────
def test_registry_is_populated() -> None:
    """The registry must expose the expected core tools (guards discovery)."""
    assert_that(TOOL_NAMES).is_not_empty()
    assert_that(TOOL_NAMES).contains("ruff", "black", "mypy", "prettier")


# ── Per-tool completeness assertions ────────────────────────────────────────
@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_manifest_entry_exists(tool: str) -> None:
    """Every non-exempt tool has a ``manifest.json`` entry.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, MANIFEST_EXEMPT)
    assert_that(_MANIFEST_TOOLS).described_as(
        f"{tool}: missing manifest.json entry",
    ).contains_key(_canonical(tool))


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_priority_is_effective(tool: str) -> None:
    """The definition's declared priority is the effective priority.

    Guards against "dead" priorities where a definition declares a value that
    ``DEFAULT_TOOL_PRIORITIES`` overrides (or omits, falling back to 50).

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, PRIORITY_EXEMPT)
    declared = _DEFINITIONS[tool].priority  # type: ignore[attr-defined]
    effective = get_tool_priority(tool)
    assert_that(effective).described_as(
        f"{tool}: declared priority {declared} != effective {effective}",
    ).is_equal_to(declared)


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_manifest_tags_match_tool_type(tool: str) -> None:
    """Manifest ``tags`` agree with the definition's ``tool_type`` flags.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, MANIFEST_EXEMPT)
    _skip_if_exempt(tool, TAGS_EXEMPT)
    canonical = _canonical(tool)
    assert_that(_MANIFEST_TOOLS).described_as(
        f"{tool}: missing manifest.json entry",
    ).contains_key(canonical)
    entry = _MANIFEST_TOOLS[canonical]
    raw_tags = entry.get("tags", [])
    manifest_tags = set(raw_tags) if isinstance(raw_tags, list) else set()
    expected = _expected_tags(_DEFINITIONS[tool].tool_type)  # type: ignore[attr-defined]
    assert_that(manifest_tags).described_as(
        f"{tool}: manifest tags {sorted(manifest_tags)} != "
        f"tool_type tags {sorted(expected)}",
    ).is_equal_to(expected)


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_install_hint_exists(tool: str) -> None:
    """Every non-exempt tool has an install hint in ``version_checking.py``.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, INSTALL_HINT_EXEMPT)
    hints = get_install_hints()
    has_hint = tool in hints or _canonical(tool) in hints or _slug(tool) in hints
    assert_that(has_hint).described_as(
        f"{tool}: no install hint in version_checking.py",
    ).is_true()


def test_install_hints_cover_all_version_keys() -> None:
    """Every ``get_minimum_versions()`` key has an install-hint template.

    Guards npm package aliases (e.g. ``@commitlint/cli``) that appear in
    version maps alongside the registry tool name (``commitlint``). A gap
    here is what made ``get_install_hints()`` emit
    ``Missing install hints for tools: ...`` (#1593).
    """
    versions = get_minimum_versions()
    hints = get_install_hints()
    missing = sorted(set(versions) - set(hints))
    assert_that(missing).described_as(
        f"version keys without install hints: {missing}",
    ).is_empty()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_tool_analysis_doc_exists(tool: str) -> None:
    """A ``docs/tool-analysis/<tool>-analysis.md`` doc exists.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, TOOL_ANALYSIS_EXEMPT)
    candidates = {f"{alias.replace(' ', '-')}-analysis.md" for alias in _aliases(tool)}
    exists = any((TOOL_ANALYSIS_DIR / name).exists() for name in candidates)
    assert_that(exists).described_as(
        f"{tool}: missing docs/tool-analysis/ doc (tried {sorted(candidates)})",
    ).is_true()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_configuration_section_exists(tool: str) -> None:
    """``docs/configuration.md`` contains a heading for the tool.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, CONFIGURATION_EXEMPT)
    assert_that(_heading_mentions(CONFIGURATION_DOC, _aliases(tool))).described_as(
        f"{tool}: no docs/configuration.md section",
    ).is_true()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_readme_table_row_exists(tool: str) -> None:
    """The README supported-tools table mentions the tool.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, README_EXEMPT)
    assert_that(_file_contains_any(README, _aliases(tool))).described_as(
        f"{tool}: not found in README",
    ).is_true()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_unit_tests_dir_exists(tool: str) -> None:
    """A ``tests/unit/tools/<tool>/`` directory exists.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, UNIT_TESTS_EXEMPT)
    canonical = _canonical(tool)
    # pytest's plugin dir is suffixed to avoid clashing with the pytest package.
    candidates = {canonical, f"{canonical}_tool"}
    exists = any((UNIT_TOOLS_DIR / name).is_dir() for name in candidates)
    assert_that(exists).described_as(
        f"{tool}: missing tests/unit/tools/{canonical}/",
    ).is_true()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_integration_test_exists(tool: str) -> None:
    """An integration test exists under ``tests/integration/tools/``.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, INTEGRATION_TESTS_EXEMPT)
    canonical = _canonical(tool)
    as_dir = (INTEGRATION_TOOLS_DIR / canonical).is_dir()
    as_file = (INTEGRATION_TOOLS_DIR / f"test_{canonical}_integration.py").exists()
    assert_that(as_dir or as_file).described_as(
        f"{tool}: missing integration test under tests/integration/tools/",
    ).is_true()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_sample_fixtures_exist(tool: str) -> None:
    """A ``test_samples/tools/<lang>/<tool>/`` fixture directory exists.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, SAMPLES_EXEMPT)
    assert_that(SAMPLES_DIR.is_dir()).described_as(
        f"sample fixtures root is missing: {SAMPLES_DIR}",
    ).is_true()
    canonical = _canonical(tool)
    matches = [p for p in SAMPLES_DIR.rglob(canonical) if p.is_dir()]
    assert_that(matches).described_as(
        f"{tool}: no test_samples/tools/**/{canonical}/ directory",
    ).is_not_empty()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_site_mirror_entry_exists(tool: str) -> None:
    """An ``apps/site`` mirror entry exists in ``sidebar-nav.ts``.

    Args:
        tool: Registry tool name (parametrized).
    """
    _skip_if_exempt(tool, SITE_MIRROR_EXEMPT)
    needles = {f"tools/{alias.replace(' ', '-')}" for alias in _aliases(tool)}
    assert_that(_file_contains_any(SITE_NAV, needles)).described_as(
        f"{tool}: no apps/site mirror entry (tried {sorted(needles)})",
    ).is_true()
