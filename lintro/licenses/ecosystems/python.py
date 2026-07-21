"""Python ecosystem adapter for collecting installed package licenses.

Uses :mod:`importlib.metadata` to read license information from the currently
resolved environment. This reflects exactly what is installed, including
transitive dependencies, without any network access.
"""

from __future__ import annotations

from email.message import Message
from importlib import metadata
from importlib.metadata import Distribution
from typing import cast

from lintro.licenses.models import PackageLicense
from lintro.licenses.spdx import normalize_to_spdx

_CLASSIFIER_PREFIX = "License :: "


def _header(meta: Message, key: str) -> str | None:
    """Read a single metadata header as a plain string.

    Args:
        meta: The distribution metadata message.
        key: Header name to read.

    Returns:
        str | None: The header value, or None if absent.
    """
    value = meta.get(key)
    return value if isinstance(value, str) else None


def _license_from_classifiers(classifiers: list[str]) -> str | None:
    """Extract a license from Trove classifiers.

    Args:
        classifiers: The ``Classifier`` metadata entries for a distribution.

    Returns:
        str | None: A raw license string (the classifier leaf), or None.
    """
    for classifier in classifiers:
        if not classifier.startswith(_CLASSIFIER_PREFIX):
            continue
        leaf = classifier.rsplit("::", 1)[-1].strip()
        # Skip the umbrella "OSI Approved" node with no specific license.
        if leaf and leaf.lower() != "osi approved":
            resolved = normalize_to_spdx(leaf)
            if resolved is not None:
                return leaf
    return None


def _raw_license(dist: Distribution) -> str | None:
    """Determine the best raw license string for a distribution.

    Resolution order: PEP 639 ``License-Expression``, the free-form
    ``License`` field, then Trove ``Classifier`` entries.

    Args:
        dist: The distribution to inspect.

    Returns:
        str | None: A raw license string, or None if nothing usable is found.
    """
    meta = cast(Message, dist.metadata)

    expression = _header(meta, "License-Expression")
    if expression and expression.strip():
        return expression.strip()

    license_field = _header(meta, "License")
    if license_field and license_field.strip():
        value = license_field.strip()
        # Some packages dump the full license text into this field; only use
        # it when it is short enough to be an identifier/name.
        if len(value) <= 64 and "\n" not in value:
            return value

    classifiers = meta.get_all("Classifier") or []
    return _license_from_classifiers([str(c) for c in classifiers])


class PythonLicenseAdapter:
    """Collect license information for installed Python packages."""

    ecosystem = "python"

    def get_installed_licenses(self) -> list[PackageLicense]:
        """Collect licenses for all installed distributions.

        Returns:
            list[PackageLicense]: One entry per installed distribution.
        """
        packages: list[PackageLicense] = []
        seen: set[str] = set()

        for dist in metadata.distributions():
            name = _header(cast(Message, dist.metadata), "Name")
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)

            raw = _raw_license(dist)
            packages.append(
                PackageLicense(
                    name=name,
                    version=dist.version or "unknown",
                    license_id=normalize_to_spdx(raw),
                    license_name=raw,
                    source_file="importlib.metadata",
                    ecosystem=self.ecosystem,
                    is_dev=False,
                ),
            )

        return sorted(packages, key=lambda p: p.name.lower())
