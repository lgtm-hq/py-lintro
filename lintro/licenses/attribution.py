"""Generate third-party attribution documents from package licenses."""

from __future__ import annotations

from lintro.licenses.models import PackageLicense

_UNKNOWN_GROUP = "Unknown"


class AttributionGenerator:
    """Produce a ``THIRD_PARTY_LICENSES.md`` document grouped by license."""

    def group_by_license(
        self,
        packages: list[PackageLicense],
    ) -> dict[str, list[PackageLicense]]:
        """Group packages by their SPDX identifier.

        Args:
            packages: Packages to group.

        Returns:
            dict[str, list[PackageLicense]]: Packages keyed by SPDX id, with
                packages lacking an identifier grouped under ``Unknown``.
        """
        groups: dict[str, list[PackageLicense]] = {}
        for package in packages:
            key = package.license_id or _UNKNOWN_GROUP
            groups.setdefault(key, []).append(package)
        for entries in groups.values():
            entries.sort(key=lambda p: p.name.lower())
        return groups

    def generate_markdown(self, packages: list[PackageLicense]) -> str:
        """Render a Markdown attribution document.

        Args:
            packages: Packages to include in the attribution.

        Returns:
            str: Markdown content suitable for ``THIRD_PARTY_LICENSES.md``.
        """
        lines: list[str] = [
            "# Third-Party Licenses",
            "",
            "This project uses the following third-party packages, grouped by license.",
            "",
        ]

        groups = self.group_by_license(packages)
        for license_id in sorted(groups.keys()):
            lines.append(f"## {license_id}")
            lines.append("")
            for package in groups[license_id]:
                display = package.license_name or license_id
                lines.append(f"- **{package.name}** ({package.version}) — {display}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
