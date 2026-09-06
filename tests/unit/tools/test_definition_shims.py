"""Guards for the per-tool package layout's re-export shims (#2311).

Tools whose implementation spans several modules live in their own package,
``lintro/tools/<name>/``, with the plugin in ``definition.py``. Plugin
discovery still scans ``lintro/tools/definitions``, so each moved tool leaves a
re-export shim behind there. These tests pin that contract: the shim must
expose the very same plugin class, and importing it must register the tool.
"""

from __future__ import annotations

import subprocess  # nosec B404 - the isolated import runs with a fixed argument list
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

from lintro.tools.actionlint import definition as actionlint_package
from lintro.tools.astro_check import definition as astro_check_package
from lintro.tools.bandit import definition as bandit_package
from lintro.tools.black import definition as black_package
from lintro.tools.buf import definition as buf_package
from lintro.tools.cargo_audit import definition as cargo_audit_package
from lintro.tools.cargo_deny import definition as cargo_deny_package
from lintro.tools.clippy import definition as clippy_package
from lintro.tools.commitlint import definition as commitlint_package
from lintro.tools.definitions import actionlint as actionlint_shim
from lintro.tools.definitions import astro_check as astro_check_shim
from lintro.tools.definitions import bandit as bandit_shim
from lintro.tools.definitions import black as black_shim
from lintro.tools.definitions import buf as buf_shim
from lintro.tools.definitions import cargo_audit as cargo_audit_shim
from lintro.tools.definitions import cargo_deny as cargo_deny_shim
from lintro.tools.definitions import clippy as clippy_shim
from lintro.tools.definitions import commitlint as commitlint_shim
from lintro.tools.definitions import dotenv_linter as dotenv_linter_shim
from lintro.tools.definitions import gitleaks as gitleaks_shim
from lintro.tools.definitions import golangci_lint as golangci_lint_shim
from lintro.tools.definitions import hadolint as hadolint_shim
from lintro.tools.definitions import html_validate as html_validate_shim
from lintro.tools.definitions import idiom_review as idiom_review_shim
from lintro.tools.definitions import import_linter as import_linter_shim
from lintro.tools.definitions import markdownlint as markdownlint_shim
from lintro.tools.definitions import mypy as mypy_shim
from lintro.tools.definitions import osv_scanner as osv_scanner_shim
from lintro.tools.definitions import oxfmt as oxfmt_shim
from lintro.tools.definitions import oxlint as oxlint_shim
from lintro.tools.definitions import oxlint_doctor as oxlint_doctor_shim
from lintro.tools.definitions import pip_audit as pip_audit_shim
from lintro.tools.definitions import prettier as prettier_shim
from lintro.tools.definitions import pytest as pytest_shim
from lintro.tools.definitions import ruff as ruff_shim
from lintro.tools.dotenv_linter import definition as dotenv_linter_package
from lintro.tools.gitleaks import definition as gitleaks_package
from lintro.tools.golangci_lint import definition as golangci_lint_package
from lintro.tools.hadolint import definition as hadolint_package
from lintro.tools.html_validate import definition as html_validate_package
from lintro.tools.idiom_review import definition as idiom_review_package
from lintro.tools.import_linter import definition as import_linter_package
from lintro.tools.markdownlint import definition as markdownlint_package
from lintro.tools.mypy import definition as mypy_package
from lintro.tools.osv_scanner import definition as osv_scanner_package
from lintro.tools.oxfmt import definition as oxfmt_package
from lintro.tools.oxlint import definition as oxlint_package
from lintro.tools.oxlint import doctor as oxlint_doctor_package
from lintro.tools.pip_audit import definition as pip_audit_package
from lintro.tools.prettier import definition as prettier_package
from lintro.tools.pytest import definition as pytest_package
from lintro.tools.ruff import definition as ruff_package

#: Repository root, so the child interpreter runs against this checkout.
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

#: Bound the child process so a hang surfaces as TimeoutExpired.
SUBPROCESS_TIMEOUT_SECONDS: int = 120

#: Program the isolated check runs: import nothing but the shim, then print the
#: registry's names. ``ToolRegistry`` only auto-discovers when it is empty, so
#: a name in this output was registered by the shim's import alone.
_ISOLATED_REGISTRATION_PROGRAM = """\
import importlib

from lintro.plugins.registry import ToolRegistry

importlib.import_module({shim!r})
print(" ".join(sorted(ToolRegistry.get_names())))
"""

#: ``(shim module, package module, plugin attribute, registered tool name)``
#: for every tool that has moved into its own package.
MOVED_TOOLS: list[tuple[ModuleType, ModuleType, str, str]] = [
    (actionlint_shim, actionlint_package, "ActionlintPlugin", "actionlint"),
    (astro_check_shim, astro_check_package, "AstroCheckPlugin", "astro-check"),
    (bandit_shim, bandit_package, "BanditPlugin", "bandit"),
    (black_shim, black_package, "BlackPlugin", "black"),
    (buf_shim, buf_package, "BufPlugin", "buf"),
    (cargo_audit_shim, cargo_audit_package, "CargoAuditPlugin", "cargo_audit"),
    (cargo_deny_shim, cargo_deny_package, "CargoDenyPlugin", "cargo_deny"),
    (clippy_shim, clippy_package, "ClippyPlugin", "clippy"),
    (commitlint_shim, commitlint_package, "CommitlintPlugin", "commitlint"),
    (dotenv_linter_shim, dotenv_linter_package, "DotenvLinterPlugin", "dotenv_linter"),
    (gitleaks_shim, gitleaks_package, "GitleaksPlugin", "gitleaks"),
    (golangci_lint_shim, golangci_lint_package, "GolangciLintPlugin", "golangci_lint"),
    (hadolint_shim, hadolint_package, "HadolintPlugin", "hadolint"),
    (html_validate_shim, html_validate_package, "HtmlValidatePlugin", "html_validate"),
    (idiom_review_shim, idiom_review_package, "IdiomReviewPlugin", "idiom-review"),
    (import_linter_shim, import_linter_package, "ImportLinterPlugin", "import-linter"),
    (markdownlint_shim, markdownlint_package, "MarkdownlintPlugin", "markdownlint"),
    (mypy_shim, mypy_package, "MypyPlugin", "mypy"),
    (osv_scanner_shim, osv_scanner_package, "OsvScannerPlugin", "osv_scanner"),
    (oxfmt_shim, oxfmt_package, "OxfmtPlugin", "oxfmt"),
    (oxlint_shim, oxlint_package, "OxlintPlugin", "oxlint"),
    (pip_audit_shim, pip_audit_package, "PipAuditPlugin", "pip_audit"),
    (prettier_shim, prettier_package, "PrettierPlugin", "prettier"),
    (pytest_shim, pytest_package, "PytestPlugin", "pytest"),
    (ruff_shim, ruff_package, "RuffPlugin", "ruff"),
]

#: Readable parametrisation ids, one per entry of :data:`MOVED_TOOLS`.
MOVED_TOOL_IDS: list[str] = [name for _, _, _, name in MOVED_TOOLS]


@pytest.mark.parametrize(
    ("shim", "package", "plugin_attr", "tool_name"),
    MOVED_TOOLS,
    ids=MOVED_TOOL_IDS,
)
def test_shim_re_exports_the_same_plugin_class(
    shim: ModuleType,
    package: ModuleType,
    plugin_attr: str,
    tool_name: str,
) -> None:
    """The shim exposes the package's plugin class, not a copy of it.

    Args:
        shim: Module under ``lintro.tools.definitions`` that discovery scans.
        package: Module inside the tool's own package.
        plugin_attr: Name of the plugin class both modules expose.
        tool_name: Registered tool name, unused by this assertion.
    """
    del tool_name

    assert_that(getattr(shim, plugin_attr)).is_same_as(
        getattr(package, plugin_attr),
    )


@pytest.mark.parametrize(
    ("shim", "package", "plugin_attr", "tool_name"),
    MOVED_TOOLS,
    ids=MOVED_TOOL_IDS,
)
def test_importing_only_the_shim_registers_the_tool(
    shim: ModuleType,
    package: ModuleType,
    plugin_attr: str,
    tool_name: str,
) -> None:
    """The shim alone registers the tool, so discovery still finds it.

    Checked in a child interpreter: importing the tool's package in this
    module would register the tool by itself and make the assertion vacuous.

    Args:
        shim: Module under ``lintro.tools.definitions`` that discovery scans.
        package: Module inside the tool's own package, unused here.
        plugin_attr: Name of the plugin class, unused here.
        tool_name: Name the tool is registered under.
    """
    del package, plugin_attr

    completed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [
            sys.executable,
            "-c",
            _ISOLATED_REGISTRATION_PROGRAM.format(shim=shim.__name__),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert_that(completed.returncode).described_as(completed.stderr).is_zero()
    assert_that(completed.stdout.split()).contains(tool_name)


def test_oxlint_doctor_shim_re_exports_the_package_module() -> None:
    """The helper shim exposes the package's objects, not copies of them.

    ``oxlint_doctor`` registers no tool: it is the oxlint package's doctor
    helper, imported by discovery only because it sits in the scanned package.
    The shim therefore has its own guard rather than a
    :data:`MOVED_TOOLS` entry. The two ``__all__`` lists are compared as well
    as the objects behind them, so the shim can neither drop a name nor grow
    one the helper does not declare.
    """
    assert_that(list(oxlint_doctor_shim.__all__)).is_equal_to(
        list(oxlint_doctor_package.__all__),
    )
    for attribute in oxlint_doctor_package.__all__:
        assert_that(getattr(oxlint_doctor_shim, attribute)).is_same_as(
            getattr(oxlint_doctor_package, attribute),
        )
