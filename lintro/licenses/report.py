"""Rendering helpers for license compliance results."""

from __future__ import annotations

import csv
import io
import json

from rich.console import Console
from rich.table import Table

from lintro.licenses.models import LicenseResult, LicenseStatus

_STATUS_STYLE: dict[LicenseStatus, str] = {
    LicenseStatus.ALLOWED: "green",
    LicenseStatus.DENIED: "red",
    LicenseStatus.UNKNOWN: "yellow",
}

_STATUS_LABEL: dict[LicenseStatus, str] = {
    LicenseStatus.ALLOWED: "✓ Allowed",
    LicenseStatus.DENIED: "✗ Denied",
    LicenseStatus.UNKNOWN: "⚠ Unknown",
}


def render_grid(console: Console, results: list[LicenseResult]) -> None:
    """Render results as a Rich table grouped by ecosystem.

    Args:
        console: The Rich console to print to.
        results: Evaluated license results.
    """
    if not results:
        console.print("[dim]No dependencies found to check.[/dim]")
        return

    ecosystems = sorted({r.package.ecosystem for r in results})
    for ecosystem in ecosystems:
        table = Table(title=f"{ecosystem} dependencies")
        table.add_column("Package", style="cyan", no_wrap=True)
        table.add_column("Version", style="dim")
        table.add_column("License", style="white")
        table.add_column("Status", justify="left")

        for result in results:
            if result.package.ecosystem != ecosystem:
                continue
            license_display = (
                result.package.license_id or result.package.license_name or "UNKNOWN"
            )
            style = _STATUS_STYLE[result.status]
            label = _STATUS_LABEL[result.status]
            table.add_row(
                result.package.name,
                result.package.version,
                license_display,
                f"[{style}]{label}[/{style}]",
            )
        console.print(table)

    _render_summary(console, results)


def _render_summary(console: Console, results: list[LicenseResult]) -> None:
    """Print an allowed/denied/unknown summary and any violations.

    Args:
        console: The Rich console to print to.
        results: Evaluated license results.
    """
    allowed = sum(1 for r in results if r.status is LicenseStatus.ALLOWED)
    denied = sum(1 for r in results if r.status is LicenseStatus.DENIED)
    unknown = sum(1 for r in results if r.status is LicenseStatus.UNKNOWN)

    console.print()
    console.print(f"Total packages: {len(results)}")
    console.print(f"[green]✓ Allowed: {allowed}[/green]")
    console.print(f"[red]✗ Denied: {denied}[/red]")
    console.print(f"[yellow]⚠ Unknown: {unknown}[/yellow]")

    violations = [r for r in results if r.status is LicenseStatus.DENIED]
    if violations:
        console.print()
        console.print("[red]Violations:[/red]")
        for result in violations:
            pkg = result.package
            license_display = pkg.license_id or pkg.license_name or "UNKNOWN"
            console.print(
                f"  - {pkg.name}@{pkg.version} ({license_display}) — {result.reason}",
            )


def to_json(results: list[LicenseResult]) -> str:
    """Serialize results as an indented JSON array.

    Args:
        results: Evaluated license results.

    Returns:
        str: JSON document.
    """
    payload = [
        {
            "name": r.package.name,
            "version": r.package.version,
            "license_id": r.package.license_id,
            "license_name": r.package.license_name,
            "ecosystem": r.package.ecosystem,
            "status": str(r.status),
            "reason": r.reason,
        }
        for r in results
    ]
    return json.dumps(payload, indent=2)


def to_csv(results: list[LicenseResult]) -> str:
    """Serialize results as CSV.

    Args:
        results: Evaluated license results.

    Returns:
        str: CSV document including a header row.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["name", "version", "license_id", "ecosystem", "status", "reason"],
    )
    for r in results:
        writer.writerow(
            [
                r.package.name,
                r.package.version,
                r.package.license_id or "",
                r.package.ecosystem,
                str(r.status),
                r.reason,
            ],
        )
    return buffer.getvalue()


def to_spdx(results: list[LicenseResult]) -> str:
    """Serialize results as a minimal SPDX 2.3 tag-value document.

    Args:
        results: Evaluated license results.

    Returns:
        str: SPDX tag-value document.
    """
    lines = [
        "SPDXVersion: SPDX-2.3",
        "DataLicense: CC0-1.0",
        "SPDXID: SPDXRef-DOCUMENT",
        "DocumentName: lintro-license-report",
        "",
    ]
    for r in results:
        spdx_id = "".join(ch for ch in r.package.name if ch.isalnum())
        concluded = r.package.license_id or "NOASSERTION"
        lines.extend(
            [
                f"PackageName: {r.package.name}",
                f"SPDXID: SPDXRef-Package-{spdx_id}",
                f"PackageVersion: {r.package.version}",
                f"PackageLicenseConcluded: {concluded}",
                f"PackageLicenseDeclared: {concluded}",
                "",
            ],
        )
    return "\n".join(lines).rstrip() + "\n"
