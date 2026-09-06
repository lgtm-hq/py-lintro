"""Doctor checks for oxlint type-aware linting support.

Type-aware linting (``oxlint --type-aware``) is an alpha feature that requires
the ``oxlint-tsgolint`` companion binary and a modern TypeScript toolchain
(``typescript`` >= 7.0). These checks surface actionable diagnostics from
``lintro doctor`` when type-aware linting is enabled, either via the ``oxlint``
``type_aware`` option or via ``options.typeAware`` in the discovered
``.oxlintrc.json``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 - argv lists run with shell=False for version checks
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from lintro.enums.tool_status import ToolStatus
from lintro.tools.core.version_parsing import compare_versions
from lintro.utils.native_parsers import load_native_tool_config

__all__ = [
    "OxlintCheckResult",
    "check_oxlint_type_aware",
    "oxlintrc_type_aware_enabled",
]

# Install hint emitted when the type-aware companion binary is unresolvable.
TSGOLINT_INSTALL_HINT = "bun add -d oxlint-tsgolint@latest"

# Minimum TypeScript version required for oxlint type-aware linting.
TYPESCRIPT_MIN_VERSION = "7.0.0"


@dataclass(frozen=True)
class OxlintCheckResult:
    """Result of a single oxlint type-aware dependency check.

    Attributes:
        name: Stable identifier for the check (e.g. ``oxlint.type-aware.tsgolint``).
        status: ToolStatus value (OK, MISSING, INCOMPATIBLE, UNKNOWN).
        message: Human-readable status message.
        hint: Optional actionable remediation hint.
    """

    name: str
    status: ToolStatus
    message: str
    hint: str = ""


def oxlintrc_type_aware_enabled() -> bool:
    """Return True when ``options.typeAware`` is enabled in ``.oxlintrc.json``.

    Returns:
        bool: True if the discovered oxlint native config sets
            ``options.typeAware`` to a truthy value.
    """
    config = load_native_tool_config("oxlint")
    options = config.get("options")
    if isinstance(options, dict):
        return bool(options.get("typeAware"))
    return False


def _resolve_tsgolint() -> str | None:
    """Resolve the ``oxlint-tsgolint`` companion binary.

    Resolution order mirrors how oxlint locates the binary: a project-local
    ``node_modules`` install first, then a binary on ``PATH``, then a ``bunx``
    fallback.

    Returns:
        str | None: A resolved command/path, or None when unresolvable.
    """
    local = Path("node_modules") / ".bin" / "oxlint-tsgolint"
    if local.exists():
        return str(local)

    found = shutil.which("oxlint-tsgolint")
    if found:
        return found

    if shutil.which("bunx"):
        # Display-only: this space-joined string documents the bunx fallback
        # command for doctor output. It must NOT be passed to subprocess as a
        # single argument; split it (e.g. ``shlex.split``) before use.
        return "bunx oxlint-tsgolint"

    return None


def _detect_typescript_version() -> str | None:
    """Detect the available TypeScript version.

    Prefers the project-local ``node_modules/typescript`` install, then falls
    back to a ``tsc --version`` invocation.

    Returns:
        str | None: The detected TypeScript version, or None when unknown.
    """
    pkg = Path("node_modules") / "typescript" / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            version = data.get("version")
            if isinstance(version, str) and version:
                return version
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(f"[oxlint doctor] Could not read typescript package: {exc}")

    tsc = shutil.which("tsc")
    if tsc:
        try:
            result = subprocess.run(  # nosec B603 - resolved binary, shell=False
                [tsc, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                match = re.search(
                    r"(\d+\.\d+(?:\.\d+)?)",
                    result.stdout + result.stderr,
                )
                if match:
                    return match.group(1)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug(f"[oxlint doctor] tsc --version failed: {exc}")

    return None


def check_oxlint_type_aware(*, option_enabled: bool = False) -> list[OxlintCheckResult]:
    """Run oxlint type-aware dependency checks when the feature is enabled.

    The feature is considered enabled when ``option_enabled`` is True (the
    ``oxlint`` ``type_aware`` option) OR when ``options.typeAware`` is set in the
    discovered ``.oxlintrc.json``.

    Args:
        option_enabled: Whether the ``type_aware`` option is enabled for oxlint.

    Returns:
        list[OxlintCheckResult]: Check results (empty when type-aware linting is
            not enabled).
    """
    if not (option_enabled or oxlintrc_type_aware_enabled()):
        return []

    results: list[OxlintCheckResult] = []

    resolved = _resolve_tsgolint()
    if resolved is None:
        results.append(
            OxlintCheckResult(
                name="oxlint.type-aware.tsgolint",
                status=ToolStatus.MISSING,
                message="oxlint-tsgolint not resolvable (node_modules / bunx)",
                hint=TSGOLINT_INSTALL_HINT,
            ),
        )
    else:
        results.append(
            OxlintCheckResult(
                name="oxlint.type-aware.tsgolint",
                status=ToolStatus.OK,
                message=f"oxlint-tsgolint resolved ({resolved})",
            ),
        )

    ts_version = _detect_typescript_version()
    if ts_version is None:
        results.append(
            OxlintCheckResult(
                name="oxlint.type-aware.typescript",
                status=ToolStatus.MISSING,
                message="TypeScript not found (>= 7.0 required)",
                hint="bun add -d typescript@latest",
            ),
        )
    else:
        try:
            below_min = compare_versions(ts_version, TYPESCRIPT_MIN_VERSION) < 0
        except ValueError:
            results.append(
                OxlintCheckResult(
                    name="oxlint.type-aware.typescript",
                    status=ToolStatus.UNKNOWN,
                    message=f"Could not parse TypeScript version '{ts_version}'",
                    hint="bun add -d typescript@latest",
                ),
            )
        else:
            if below_min:
                results.append(
                    OxlintCheckResult(
                        name="oxlint.type-aware.typescript",
                        status=ToolStatus.INCOMPATIBLE,
                        message=(
                            f"TypeScript {ts_version} < {TYPESCRIPT_MIN_VERSION} "
                            "required for type-aware linting"
                        ),
                        hint="bun add -d typescript@latest",
                    ),
                )
            else:
                results.append(
                    OxlintCheckResult(
                        name="oxlint.type-aware.typescript",
                        status=ToolStatus.OK,
                        message=(
                            f"TypeScript {ts_version} " f"(>= {TYPESCRIPT_MIN_VERSION})"
                        ),
                    ),
                )

    return results
