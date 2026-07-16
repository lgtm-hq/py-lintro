"""``lintro licenses`` command for dependency license compliance."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from lintro.config.licenses_config import load_licenses_config
from lintro.licenses.attribution import AttributionGenerator
from lintro.licenses.ecosystems import NpmLicenseAdapter, PythonLicenseAdapter
from lintro.licenses.models import LicenseResult, LicenseStatus, PackageLicense
from lintro.licenses.policy_engine import LicensePolicyEngine
from lintro.licenses.report import render_grid, to_csv, to_json, to_spdx

_SUPPORTED_LANGS = ("python", "npm")


def _collect_packages(langs: tuple[str, ...]) -> list[PackageLicense]:
    """Collect package licenses across the requested ecosystems.

    Args:
        langs: Ecosystems to scan. Empty means all supported ecosystems.

    Returns:
        list[PackageLicense]: Discovered packages.
    """
    selected = set(langs) if langs else set(_SUPPORTED_LANGS)
    packages: list[PackageLicense] = []

    if "python" in selected:
        packages.extend(PythonLicenseAdapter().get_installed_licenses())

    if "npm" in selected:
        package_json = Path.cwd() / "package.json"
        packages.extend(
            NpmLicenseAdapter().get_licenses_from_package_json(package_json),
        )

    return packages


@click.command()
@click.option(
    "--check",
    is_flag=True,
    help="Exit non-zero when policy violations are found.",
)
@click.option(
    "--lang",
    "-l",
    "langs",
    multiple=True,
    type=click.Choice(_SUPPORTED_LANGS),
    help="Restrict scanning to specific ecosystem(s).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["grid", "json", "csv", "spdx"]),
    default="grid",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--problems-only",
    is_flag=True,
    help="Show only denied or unknown licenses.",
)
@click.option(
    "--attribution",
    is_flag=True,
    help="Generate a THIRD_PARTY_LICENSES.md attribution document to stdout.",
)
def licenses_command(
    check: bool,
    langs: tuple[str, ...],
    output_format: str,
    problems_only: bool,
    attribution: bool,
) -> None:
    """Check dependency licenses for policy compliance.

    Scans resolved dependencies, normalizes their licenses to SPDX
    identifiers, and evaluates them against the configured allow/deny policy
    (``[tool.lintro.licenses]`` or the ``licenses:`` section of
    ``.lintro-config.yaml``).

    \f

    Args:
        check: Exit non-zero when any violation is present.
        langs: Ecosystems to scan; empty scans all supported ecosystems.
        output_format: One of ``grid``, ``json``, ``csv``, or ``spdx``.
        problems_only: Only include denied/unknown results in the output.
        attribution: Emit a Markdown attribution document instead of a report.

    Raises:
        SystemExit: When ``check`` is set and policy violations are found.
    """
    console = Console()
    config = load_licenses_config()
    engine = LicensePolicyEngine(config)

    packages = _collect_packages(langs)

    if attribution:
        content = AttributionGenerator().generate_markdown(packages)
        click.echo(content, nl=False)
        return

    results = engine.evaluate_all(packages)

    if problems_only:
        results = [r for r in results if r.status is not LicenseStatus.ALLOWED]

    _emit(console, results, output_format)

    if check and _has_violations(results):
        raise SystemExit(1)


def _emit(
    console: Console,
    results: list[LicenseResult],
    output_format: str,
) -> None:
    """Render results in the requested format.

    Args:
        console: The Rich console used for grid output.
        results: Evaluated license results.
        output_format: The chosen output format.
    """
    if output_format == "json":
        click.echo(to_json(results))
    elif output_format == "csv":
        click.echo(to_csv(results), nl=False)
    elif output_format == "spdx":
        click.echo(to_spdx(results), nl=False)
    else:
        render_grid(console, results)


def _has_violations(results: list[LicenseResult]) -> bool:
    """Return whether any result is a policy violation.

    Args:
        results: Evaluated license results.

    Returns:
        bool: True if at least one result is DENIED.
    """
    return any(r.status is LicenseStatus.DENIED for r in results)
