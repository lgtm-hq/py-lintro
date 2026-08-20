"""Canonical tool name to ecosystem package name resolution.

Lintro identifies tools by a canonical Python-friendly name (``golangci_lint``),
but package managers use their own spelling (``golangci-lint``). Resolving that
mapping in one place keeps install commands, upgrade commands, generated hints
and script-backed fallbacks from drifting apart — a drift that previously
produced ``brew install golangci_lint`` and a non-convergent retry loop.
"""

from __future__ import annotations

# Homebrew formula names, keyed by canonical lintro tool name.
#
# Every ``binary`` tool in the manifest MUST have an entry here (enforced by
# tests) so that adding a binary tool forces an explicit formula decision
# instead of silently falling back to the canonical name.
#
# Entries for non-binary tools (pip/npm) additionally act as the signal that
# Homebrew is an acceptable source for that tool: ``PipStrategy`` and
# ``NpmStrategy`` only consider brew for tools listed here.
BREW_FORMULA_NAMES: dict[str, str] = {
    # Binary tools
    "actionlint": "actionlint",
    "dotenv_linter": "dotenv-linter",
    "gitleaks": "gitleaks",
    "golangci_lint": "golangci-lint",
    "hadolint": "hadolint",
    "osv_scanner": "osv-scanner",
    "phpstan": "phpstan",
    "shellcheck": "shellcheck",
    "shfmt": "shfmt",
    "taplo": "taplo",
    "trufflehog": "trufflehog",
    "vale": "vale",
    # Non-binary tools that Homebrew can also provide
    "markdownlint": "markdownlint-cli2",
}


def canonical_to_hyphenated(tool_name: str) -> str:
    """Convert a canonical tool name to its hyphenated spelling.

    Args:
        tool_name: Canonical lintro tool name (e.g. ``"golangci_lint"``).

    Returns:
        Hyphenated name (e.g. ``"golangci-lint"``).
    """
    return tool_name.replace("_", "-")


def brew_formula_name(
    tool_name: str,
    install_package: str | None = None,
) -> str:
    """Resolve the Homebrew formula name for a canonical tool name.

    Args:
        tool_name: Canonical lintro tool name.
        install_package: Manifest package override, if any.

    Returns:
        Homebrew formula name to pass to ``brew install``/``brew upgrade``.
    """
    mapped = BREW_FORMULA_NAMES.get(tool_name)
    if mapped:
        return mapped
    if install_package:
        return canonical_to_hyphenated(install_package)
    return canonical_to_hyphenated(tool_name)


def ecosystem_package_name(
    tool_name: str,
    install_package: str | None = None,
) -> str:
    """Resolve the package name used by pip/npm/cargo for a tool.

    Args:
        tool_name: Canonical lintro tool name.
        install_package: Manifest package override, if any.

    Returns:
        The manifest override when set, otherwise the hyphenated canonical
        name (package registries do not use lintro's underscore spelling).
    """
    return install_package or canonical_to_hyphenated(tool_name)


def script_tool_name(tool_name: str) -> str:
    """Resolve the ``--tools`` argument used by ``install-tools.sh``.

    Args:
        tool_name: Canonical lintro tool name.

    Returns:
        Tool identifier understood by the install script.
    """
    return canonical_to_hyphenated(tool_name)
