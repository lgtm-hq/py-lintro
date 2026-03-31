"""Tests for tool doc_url() methods.

Verifies that each tool plugin returns the correct documentation URL for
a given rule code, and returns None for empty or invalid codes.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.actionlint import ActionlintPlugin
from lintro.tools.definitions.astro_check import AstroCheckPlugin
from lintro.tools.definitions.bandit import BanditPlugin
from lintro.tools.definitions.cargo_audit import CargoAuditPlugin
from lintro.tools.definitions.cargo_deny import CargoDenyPlugin
from lintro.tools.definitions.clippy import ClippyPlugin
from lintro.tools.definitions.hadolint import HadolintPlugin
from lintro.tools.definitions.markdownlint import MarkdownlintPlugin
from lintro.tools.definitions.mypy import MypyPlugin
from lintro.tools.definitions.osv_scanner import OsvScannerPlugin
from lintro.tools.definitions.oxlint import OxlintPlugin
from lintro.tools.definitions.pydoclint import PydoclintPlugin
from lintro.tools.definitions.ruff import RuffPlugin
from lintro.tools.definitions.semgrep import SemgrepPlugin
from lintro.tools.definitions.shellcheck import ShellcheckPlugin
from lintro.tools.definitions.sqlfluff import SqlfluffPlugin
from lintro.tools.definitions.taplo import TaploPlugin
from lintro.tools.definitions.tsc import TscPlugin
from lintro.tools.definitions.vue_tsc import VueTscPlugin
from lintro.tools.definitions.yamllint import YamllintPlugin

# =============================================================================
# Simple URL-pattern tools (no subprocess needed)
# =============================================================================


@pytest.mark.parametrize(
    ("plugin_cls", "code", "expected_url"),
    [
        # actionlint — single-page docs
        (
            ActionlintPlugin,
            "syntax",
            "https://github.com/rhysd/actionlint/blob/main/docs/checks.md",
        ),
        (ActionlintPlugin, "", None),
        # astro-check — single-page docs
        (
            AstroCheckPlugin,
            "TS2322",
            "https://docs.astro.build/en/guides/typescript/",
        ),
        (AstroCheckPlugin, "", None),
        # bandit — plugins index
        (
            BanditPlugin,
            "B101",
            "https://bandit.readthedocs.io/en/latest/plugins/index.html",
        ),
        (BanditPlugin, "", None),
        # cargo-audit — per-advisory URL
        (
            CargoAuditPlugin,
            "RUSTSEC-2021-0124",
            "https://rustsec.org/advisories/RUSTSEC-2021-0124",
        ),
        (CargoAuditPlugin, "", None),
        # cargo-deny — single-page docs
        (
            CargoDenyPlugin,
            "L001",
            "https://embarkstudios.github.io/cargo-deny/",
        ),
        (CargoDenyPlugin, "", None),
        # clippy — fragment anchor
        (
            ClippyPlugin,
            "needless_return",
            "https://rust-lang.github.io/rust-clippy/master/index.html#needless_return",
        ),
        (ClippyPlugin, "", None),
        # markdownlint — per-rule doc page (lowercased)
        (
            MarkdownlintPlugin,
            "MD013",
            "https://github.com/DavidAnson/markdownlint/blob/main/doc/md013.md",
        ),
        (MarkdownlintPlugin, "", None),
        # mypy — error code list page
        (
            MypyPlugin,
            "import-untyped",
            "https://mypy.readthedocs.io/en/stable/error_code_list.html",
        ),
        (MypyPlugin, "", None),
        # osv-scanner — per-vulnerability URL
        (
            OsvScannerPlugin,
            "GHSA-c3g4-w6cv-6v7h",
            "https://osv.dev/vulnerability/GHSA-c3g4-w6cv-6v7h",
        ),
        (OsvScannerPlugin, "", None),
        # pydoclint — single config page
        (
            PydoclintPlugin,
            "DOC301",
            "https://jsh9.github.io/pydoclint/how_to_config.html",
        ),
        (PydoclintPlugin, "", None),
        # shellcheck — wiki page
        (ShellcheckPlugin, "SC2086", "https://www.shellcheck.net/wiki/SC2086"),
        (ShellcheckPlugin, "", None),
        # sqlfluff — rules anchor
        (SqlfluffPlugin, "LT01", "https://docs.sqlfluff.com/en/stable/rules.html#LT01"),
        (SqlfluffPlugin, "", None),
        # taplo — single-page docs
        (TaploPlugin, "invalid_value", "https://taplo.tamasfe.dev/"),
        (TaploPlugin, "", None),
        # yamllint — rules anchor
        (
            YamllintPlugin,
            "line-length",
            "https://yamllint.readthedocs.io/en/stable/rules.html#line-length",
        ),
        (YamllintPlugin, "", None),
    ],
    ids=[
        "actionlint-valid",
        "actionlint-empty",
        "astro-check-valid",
        "astro-check-empty",
        "bandit-valid",
        "bandit-empty",
        "cargo-audit-valid",
        "cargo-audit-empty",
        "cargo-deny-valid",
        "cargo-deny-empty",
        "clippy-valid",
        "clippy-empty",
        "markdownlint-valid",
        "markdownlint-empty",
        "mypy-valid",
        "mypy-empty",
        "osv-scanner-valid",
        "osv-scanner-empty",
        "pydoclint-valid",
        "pydoclint-empty",
        "shellcheck-valid",
        "shellcheck-empty",
        "sqlfluff-valid",
        "sqlfluff-empty",
        "taplo-valid",
        "taplo-empty",
        "yamllint-valid",
        "yamllint-empty",
    ],
)
def test_simple_doc_url(
    plugin_cls: type,
    code: str,
    expected_url: str | None,
) -> None:
    """Verify simple URL-pattern tools return correct doc URLs.

    Args:
        plugin_cls: Plugin class to instantiate.
        code: Rule code to look up.
        expected_url: Expected documentation URL or None.
    """
    plugin = plugin_cls()
    assert_that(plugin.doc_url(code)).is_equal_to(expected_url)


# =============================================================================
# Hadolint — DL/SC routing
# =============================================================================


def test_hadolint_dl_code_returns_hadolint_wiki() -> None:
    """DL-prefixed codes route to hadolint GitHub wiki."""
    plugin = HadolintPlugin()
    assert_that(plugin.doc_url("DL3008")).is_equal_to(
        "https://github.com/hadolint/hadolint/wiki/DL3008",
    )


def test_hadolint_sc_code_returns_shellcheck_wiki() -> None:
    """SC-prefixed codes route to shellcheck.net wiki."""
    plugin = HadolintPlugin()
    assert_that(plugin.doc_url("SC2046")).is_equal_to(
        "https://www.shellcheck.net/wiki/SC2046",
    )


def test_hadolint_lowercase_dl_code_uppercased() -> None:
    """Lowercase DL codes are uppercased in the URL."""
    plugin = HadolintPlugin()
    assert_that(plugin.doc_url("dl3008")).is_equal_to(
        "https://github.com/hadolint/hadolint/wiki/DL3008",
    )


def test_hadolint_unknown_prefix_returns_none() -> None:
    """Codes with unknown prefixes return None."""
    plugin = HadolintPlugin()
    assert_that(plugin.doc_url("XX123")).is_none()


def test_hadolint_empty_code_returns_none() -> None:
    """Empty codes return None."""
    plugin = HadolintPlugin()
    assert_that(plugin.doc_url("")).is_none()


# =============================================================================
# Oxlint — category/rule format
# =============================================================================


def test_oxlint_category_rule_format() -> None:
    """Codes with category/rule format return oxc.rs URL."""
    plugin = OxlintPlugin()
    assert_that(
        plugin.doc_url("deepscan/bad-comparison-sequence"),
    ).is_equal_to(
        "https://oxc.rs/docs/guide/usage/linter/rules/deepscan/bad-comparison-sequence",
    )


def test_oxlint_no_slash_returns_none() -> None:
    """Codes without a slash return None."""
    plugin = OxlintPlugin()
    assert_that(plugin.doc_url("no-unused-vars")).is_none()


def test_oxlint_empty_code_returns_none() -> None:
    """Empty codes return None."""
    plugin = OxlintPlugin()
    assert_that(plugin.doc_url("")).is_none()


# =============================================================================
# TSC — TS prefix stripping and numeric validation
# =============================================================================


def test_tsc_ts_prefixed_code() -> None:
    """TS-prefixed codes return typescript.tv URL."""
    plugin = TscPlugin()
    assert_that(plugin.doc_url("TS2307")).is_equal_to(
        "https://typescript.tv/errors/#ts2307",
    )


def test_tsc_numeric_only_code() -> None:
    """Numeric-only codes work without TS prefix."""
    plugin = TscPlugin()
    assert_that(plugin.doc_url("2307")).is_equal_to(
        "https://typescript.tv/errors/#ts2307",
    )


def test_tsc_non_numeric_code_returns_none() -> None:
    """Non-numeric codes return None."""
    plugin = TscPlugin()
    assert_that(plugin.doc_url("TSfoo")).is_none()


def test_tsc_empty_code_returns_none() -> None:
    """Empty codes return None."""
    plugin = TscPlugin()
    assert_that(plugin.doc_url("")).is_none()


# =============================================================================
# Vue-tsc — TS prefix stripping (same logic as TSC)
# =============================================================================


def test_vue_tsc_ts_prefixed_code() -> None:
    """TS-prefixed codes return typescript.tv URL."""
    plugin = VueTscPlugin()
    assert_that(plugin.doc_url("TS2322")).is_equal_to(
        "https://typescript.tv/errors/#ts2322",
    )


def test_vue_tsc_numeric_only_code() -> None:
    """Numeric-only codes work without TS prefix."""
    plugin = VueTscPlugin()
    assert_that(plugin.doc_url("2322")).is_equal_to(
        "https://typescript.tv/errors/#ts2322",
    )


def test_vue_tsc_non_numeric_code_returns_none() -> None:
    """Non-numeric codes return None."""
    plugin = VueTscPlugin()
    assert_that(plugin.doc_url("TSfoo")).is_none()


def test_vue_tsc_empty_code_returns_none() -> None:
    """Empty codes return None."""
    plugin = VueTscPlugin()
    assert_that(plugin.doc_url("")).is_none()


# =============================================================================
# Semgrep — registry vs. custom rule detection
# =============================================================================


def test_semgrep_registry_rule_id() -> None:
    """Dotted registry rule IDs return semgrep.dev URL."""
    plugin = SemgrepPlugin()
    assert_that(
        plugin.doc_url("python.lang.security.insecure-random"),
    ).is_equal_to(
        "https://semgrep.dev/r/python.lang.security.insecure-random",
    )


def test_semgrep_local_rule_with_slash_returns_none() -> None:
    """Custom rules with path separators return None."""
    plugin = SemgrepPlugin()
    assert_that(plugin.doc_url("rules/custom-rule")).is_none()


def test_semgrep_simple_name_without_dot_returns_none() -> None:
    """Simple names without dots return None (likely local rules)."""
    plugin = SemgrepPlugin()
    assert_that(plugin.doc_url("my-custom-rule")).is_none()


def test_semgrep_empty_code_returns_none() -> None:
    """Empty codes return None."""
    plugin = SemgrepPlugin()
    assert_that(plugin.doc_url("")).is_none()


# =============================================================================
# Ruff — subprocess-based rule name resolution with caching
# =============================================================================


@patch("subprocess.run")
def test_ruff_resolves_rule_name_to_url(mock_run: MagicMock) -> None:
    """Valid codes resolve to ruff docs URL via subprocess.

    Args:
        mock_run: Mocked subprocess.run.
    """
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({"name": "line-too-long"}),
    )
    plugin = RuffPlugin()

    result = plugin.doc_url("E501")

    assert_that(result).is_equal_to(
        "https://docs.astral.sh/ruff/rules/line-too-long/",
    )


@patch("subprocess.run")
def test_ruff_caches_resolved_name(mock_run: MagicMock) -> None:
    """Second call for same code uses cache, not subprocess.

    Args:
        mock_run: Mocked subprocess.run.
    """
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({"name": "line-too-long"}),
    )
    plugin = RuffPlugin()

    plugin.doc_url("E501")
    plugin.doc_url("E501")

    assert_that(mock_run.call_count).is_equal_to(1)


@patch("subprocess.run")
def test_ruff_timeout_returns_none_and_caches(mock_run: MagicMock) -> None:
    """Subprocess timeout returns None and caches the failure.

    Args:
        mock_run: Mocked subprocess.run.
    """
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="ruff", timeout=5)
    plugin = RuffPlugin()

    result1 = plugin.doc_url("E501")
    result2 = plugin.doc_url("E501")

    assert_that(result1).is_none()
    assert_that(result2).is_none()
    assert_that(mock_run.call_count).is_equal_to(1)


@patch("subprocess.run")
def test_ruff_json_error_returns_none(mock_run: MagicMock) -> None:
    """Malformed JSON from subprocess returns None.

    Args:
        mock_run: Mocked subprocess.run.
    """
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="not json",
    )
    plugin = RuffPlugin()

    result = plugin.doc_url("E501")

    assert_that(result).is_none()


@patch("subprocess.run")
def test_ruff_nonzero_exit_returns_none(mock_run: MagicMock) -> None:
    """Non-zero exit code from subprocess returns None.

    Args:
        mock_run: Mocked subprocess.run.
    """
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
    )
    plugin = RuffPlugin()

    result = plugin.doc_url("UNKNOWN")

    assert_that(result).is_none()


def test_ruff_empty_code_returns_none() -> None:
    """Empty codes return None without calling subprocess."""
    plugin = RuffPlugin()
    assert_that(plugin.doc_url("")).is_none()
