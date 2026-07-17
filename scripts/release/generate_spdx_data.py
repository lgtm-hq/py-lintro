#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Generate ``lintro/licenses/_spdx_data.py`` from the official SPDX license list.

Fetches (or reads) the versioned SPDX ``licenses.json`` and writes a deterministic
Python data module embedding every SPDX license identifier plus per-ID flags
(``isOsiApproved``, ``isFsfLibre``, ``isDeprecatedLicenseId``).

Modes:
    default: write the generated module, exit 0.
    --check: exit 1 with a unified diff if writing would change anything.
    --from-file PATH: read JSON from PATH instead of fetching upstream
        (used by tests and offline regeneration).

Stdlib-only so the release workflow can run it without installing project deps.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INPUT_ERROR = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "lintro" / "licenses" / "_spdx_data.py"
SPDX_LICENSES_URL = "https://spdx.org/licenses/licenses.json"

# Keep the HTTP fetch bounded so a hung release job fails fast.
_FETCH_TIMEOUT_SECONDS = 60


def fetch_licenses_json() -> dict[str, Any]:
    """Download and parse the official SPDX licenses index.

    Returns:
        Parsed JSON object.

    Raises:
        RuntimeError: If the download or JSON parse fails.
    """
    # URL is a module constant (not user-controlled); urllib is used because this
    # script is stdlib-only for the release workflow.
    request = urllib.request.Request(
        SPDX_LICENSES_URL,
        headers={"User-Agent": "lintro-spdx-codegen/1.0"},
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — fixed HTTPS SPDX URL  # nosemgrep: dynamic-urllib-use-detected  # nosec B310
            request,
            timeout=_FETCH_TIMEOUT_SECONDS,
        ) as response:
            payload = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Failed to fetch SPDX licenses from {SPDX_LICENSES_URL}: {exc}",
        ) from exc
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"SPDX licenses response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("SPDX licenses JSON root must be an object")
    return data


def load_licenses_json(path: Path) -> dict[str, Any]:
    """Load SPDX licenses JSON from a local file.

    Args:
        path: Path to a licenses.json document.

    Returns:
        Parsed JSON object.

    Raises:
        RuntimeError: If the file cannot be read or parsed.
    """
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError as exc:
        raise RuntimeError(f"Could not read SPDX JSON at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid SPDX JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("SPDX licenses JSON root must be an object")
    return data


def render_spdx_data_module(data: dict[str, Any]) -> str:
    """Render the deterministic ``_spdx_data.py`` module text.

    Args:
        data: Parsed SPDX licenses.json object.

    Returns:
        Complete Python module source.

    Raises:
        RuntimeError: If required fields are missing or malformed.
    """
    licenses = data.get("licenses")
    if not isinstance(licenses, list) or not licenses:
        raise RuntimeError("SPDX licenses.json missing a non-empty 'licenses' array")

    version = data.get("licenseListVersion", "unknown")
    release_date = data.get("releaseDate", "unknown")

    entries: list[tuple[str, bool, bool | None, bool]] = []
    for item in licenses:
        if not isinstance(item, dict):
            raise RuntimeError("Each SPDX license entry must be an object")
        license_id = item.get("licenseId")
        if not isinstance(license_id, str) or not license_id:
            raise RuntimeError("SPDX license entry missing licenseId")
        is_osi = bool(item.get("isOsiApproved", False))
        is_deprecated = bool(item.get("isDeprecatedLicenseId", False))
        # isFsfLibre is omitted by upstream for many IDs; preserve None vs bool.
        if "isFsfLibre" in item:
            is_fsf: bool | None = bool(item["isFsfLibre"])
        else:
            is_fsf = None
        entries.append((license_id, is_osi, is_fsf, is_deprecated))

    entries.sort(key=lambda row: row[0])

    lines: list[str] = [
        '"""Auto-generated SPDX license identifiers. Do not edit by hand.',
        "",
        "Run ``python3 scripts/release/generate_spdx_data.py`` to regenerate.",
        "",
        f"Source: {SPDX_LICENSES_URL}",
        f"licenseListVersion: {version}",
        f"releaseDate: {release_date}",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f'SPDX_LIST_VERSION: str = "{version}"',
        f'SPDX_LIST_RELEASE_DATE: str = "{release_date}"',
        "",
        "SPDX_LICENSE_IDS: frozenset[str] = frozenset(",
        "    {",
    ]
    for license_id, _, _, _ in entries:
        lines.append(f'        "{license_id}",')
    lines.extend(
        [
            "    },",
            ")",
            "",
            "# Per-ID flags: (is_osi_approved, is_fsf_libre, is_deprecated).",
            "# is_fsf_libre is None when upstream omits the field.",
            "SPDX_LICENSE_FLAGS: dict[str, tuple[bool, bool | None, bool]] = {",
        ],
    )
    for license_id, is_osi, is_fsf, is_deprecated in entries:
        fsf_repr = "None" if is_fsf is None else str(is_fsf)
        lines.append(
            f'    "{license_id}": ({is_osi}, {fsf_repr}, {is_deprecated}),',
        )
    lines.extend(
        [
            "}",
            "",
        ],
    )
    return "\n".join(lines)


def diff_text(*, label: str, current: str, desired: str) -> str:
    """Return a unified diff between current and desired text, or empty.

    Args:
        label: File label used in the unified-diff header.
        current: Current file contents.
        desired: Desired file contents.

    Returns:
        Unified diff string, or empty when ``current == desired``.
    """
    if current == desired:
        return ""
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        desired.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    return "".join(diff)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Generate lintro/licenses/_spdx_data.py from SPDX licenses.json.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 with a diff if the output would change; do not write.",
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        help="Read licenses.json from PATH instead of fetching upstream.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help="Output path for the generated module (default: repo path).",
    )
    args = parser.parse_args(argv)

    try:
        if args.from_file is not None:
            data = load_licenses_json(args.from_file)
        else:
            data = fetch_licenses_json()
        generated = render_spdx_data_module(data)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    output_path: Path = args.output
    current = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    rel_label = (
        str(output_path.relative_to(REPO_ROOT))
        if output_path.is_relative_to(REPO_ROOT)
        else str(output_path)
    )
    drift = diff_text(label=rel_label, current=current, desired=generated)

    if args.check:
        if drift:
            sys.stdout.write(drift)
            print(
                "\nDrift detected. Run scripts/release/generate_spdx_data.py "
                "to regenerate.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        return EXIT_OK

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generated, encoding="utf-8")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
