"""Resolve per-tool update channels and version advisories.

Given a tool binary path, detect how the tool was installed (Homebrew, uv
tool, pip/venv, npm/bun, cargo, rustup, standalone) and map that channel to
an actionable update command. "Latest known" versions come from pinned
manifest / tool-versions data — this module never makes network calls.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core.install_strategies.brew_names import BREW_FORMULA_NAMES

# Per-tool channel overrides when path heuristics are wrong or unavailable.
# Keys are canonical tool names; values are UpdateChannel members (or their
# string values). Kept data-driven so callers / manifest can extend without
# branching in detect_update_channel.
TOOL_CHANNEL_OVERRIDES: dict[str, UpdateChannel] = {}


@dataclass(frozen=True)
class VersionAdvisory:
    """Structured "update available" advisory for a single tool.

    Attributes:
        tool: Canonical tool name.
        installed: Currently installed version string.
        latest_known: Pinned expected/recommended version (no network).
        channel: Detected install channel.
        update_command: Exact shell command to upgrade, or None when unknown.
    """

    tool: str
    installed: str
    latest_known: str
    channel: UpdateChannel
    update_command: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Serialize for JSON / MCP surfaces.

        Returns:
            Dictionary with string channel value and optional update command.
        """
        data = asdict(self)
        data["channel"] = self.channel.value
        return data


def detect_update_channel(
    binary_path: str | Path | None,
    *,
    tool_name: str | None = None,
    channel_override: UpdateChannel | str | None = None,
) -> UpdateChannel:
    """Detect the install channel for a tool binary.

    Resolves symlinks before matching path heuristics so Homebrew Cellar
    installs (often linked from ``/usr/local/bin`` or ``/opt/homebrew/bin``)
    are classified correctly. Per-tool overrides win over heuristics.

    Args:
        binary_path: Absolute or relative path to the tool binary.
        tool_name: Canonical tool name used for override lookup.
        channel_override: Explicit channel (e.g. from manifest); wins first.

    Returns:
        Detected :class:`UpdateChannel`. Unknown paths degrade to
        ``UNKNOWN`` (or ``STANDALONE`` for common system bin prefixes).
    """
    override = _coerce_channel(channel_override)
    if override is not None:
        return override

    if tool_name:
        mapped = TOOL_CHANNEL_OVERRIDES.get(tool_name)
        if mapped is not None:
            return mapped

    if not binary_path:
        return UpdateChannel.UNKNOWN

    resolved = _resolve_binary_path(binary_path)
    if resolved is None:
        return UpdateChannel.UNKNOWN

    path_lower = resolved.as_posix().lower()
    parts_lower = {part.lower() for part in resolved.parts}

    # Homebrew before node_modules: brew formulae for JS tools live under
    # Cellar/.../libexec/lib/node_modules/...
    if _is_homebrew_path(path_lower=path_lower, parts_lower=parts_lower):
        return UpdateChannel.HOMEBREW

    if _is_uv_tool_path(resolved=resolved, path_lower=path_lower):
        return UpdateChannel.UV_TOOL

    if _is_cargo_path(path_lower=path_lower):
        return UpdateChannel.CARGO

    if _is_rustup_path(path_lower=path_lower, parts_lower=parts_lower):
        return UpdateChannel.RUSTUP

    if _is_bun_path(path_lower=path_lower, parts_lower=parts_lower):
        return UpdateChannel.BUN

    if "node_modules" in parts_lower:
        return UpdateChannel.NPM

    if _is_pip_path(path_lower=path_lower, parts_lower=parts_lower):
        return UpdateChannel.PIP

    if _is_standalone_path(path_lower=path_lower):
        return UpdateChannel.STANDALONE

    return UpdateChannel.UNKNOWN


def resolve_update_command(
    *,
    channel: UpdateChannel,
    tool_name: str,
    install_package: str | None = None,
    latest_known: str | None = None,
) -> str | None:
    """Map an install channel to an actionable update command.

    Args:
        channel: Detected (or overridden) install channel.
        tool_name: Canonical tool name.
        install_package: Package name override from the manifest.
        latest_known: Pinned expected version for channels that pin on upgrade.

    Returns:
        Shell command string, or None when the channel has no known template.
    """
    package = _package_for_channel(
        channel=channel,
        tool_name=tool_name,
        install_package=install_package,
    )
    version = latest_known or ""

    if channel == UpdateChannel.HOMEBREW:
        return f"brew upgrade {package}"
    if channel == UpdateChannel.UV_TOOL:
        return f"uv tool upgrade {package}"
    if channel == UpdateChannel.PIP:
        if version:
            return f"uv pip install --upgrade '{package}>={version}'"
        return f"uv pip install --upgrade {package}"
    if channel == UpdateChannel.NPM:
        if version:
            return f"npm install -g {package}@{version}"
        return f"npm install -g {package}"
    if channel == UpdateChannel.BUN:
        if version:
            return f"bun add -g {package}@{version}"
        return f"bun add -g {package}"
    if channel == UpdateChannel.CARGO:
        return f"cargo install --force {package}"
    if channel == UpdateChannel.RUSTUP:
        return "rustup update stable"
    # STANDALONE / UNKNOWN: no safe one-liner
    return None


def build_version_advisory(
    *,
    tool: str,
    installed: str,
    latest_known: str,
    binary_path: str | Path | None = None,
    install_package: str | None = None,
    channel_override: UpdateChannel | str | None = None,
) -> VersionAdvisory:
    """Build a structured version advisory for an outdated tool.

    Args:
        tool: Canonical tool name.
        installed: Currently installed version.
        latest_known: Pinned expected/recommended version.
        binary_path: Path to the installed binary (for channel detection).
        install_package: Manifest package name override.
        channel_override: Explicit channel from manifest / caller.

    Returns:
        :class:`VersionAdvisory` with channel and optional update command.
    """
    channel = detect_update_channel(
        binary_path,
        tool_name=tool,
        channel_override=channel_override,
    )
    update_command = resolve_update_command(
        channel=channel,
        tool_name=tool,
        install_package=install_package,
        latest_known=latest_known,
    )
    return VersionAdvisory(
        tool=tool,
        installed=installed,
        latest_known=latest_known,
        channel=channel,
        update_command=update_command,
    )


def format_advisory_line(advisory: VersionAdvisory) -> str:
    """Render a human-readable advisory line.

    Args:
        advisory: Structured advisory to format.

    Returns:
        Single-line advisory matching doctor / versions output style.
    """
    base = (
        f"{advisory.tool} {advisory.installed} installed, "
        f"{advisory.latest_known} expected"
    )
    channel_label = advisory.channel.value.replace("_", " ")
    if advisory.update_command:
        return f"{base} — installed via {channel_label}: {advisory.update_command}"
    if advisory.channel in (UpdateChannel.UNKNOWN, UpdateChannel.STANDALONE):
        return f"{base} — update channel unknown"
    return f"{base} — installed via {channel_label}"


def channel_from_install_type(install_type: str | None) -> UpdateChannel | None:
    """Map a manifest ``install.type`` to a default update channel.

    Used as a soft fallback when path detection returns UNKNOWN.

    Args:
        install_type: Manifest install type string (pip, npm, binary, ...).

    Returns:
        Matching channel, or None when there is no sensible default.
    """
    if not install_type:
        return None
    mapping: dict[str, UpdateChannel] = {
        "pip": UpdateChannel.PIP,
        "npm": UpdateChannel.NPM,
        "cargo": UpdateChannel.CARGO,
        "rustup": UpdateChannel.RUSTUP,
        # binary stays unknown without path evidence — brew vs download
    }
    return mapping.get(install_type)


def _coerce_channel(
    value: UpdateChannel | str | None,
) -> UpdateChannel | None:
    """Coerce a string or enum into UpdateChannel."""
    if value is None:
        return None
    if isinstance(value, UpdateChannel):
        return value
    try:
        return UpdateChannel(value)
    except ValueError:
        normalized = value.lower().replace("-", "_")
        try:
            return UpdateChannel(normalized)
        except ValueError:
            return None


def _resolve_binary_path(binary_path: str | Path) -> Path | None:
    """Resolve a binary path, following symlinks when possible."""
    try:
        path = Path(binary_path).expanduser()
        if not path.is_absolute():
            path = path.resolve()
        else:
            try:
                path = path.resolve()
            except OSError:
                path = Path(os.path.normpath(path))
        return path
    except (OSError, RuntimeError, ValueError):
        return None


def _is_homebrew_path(*, path_lower: str, parts_lower: set[str]) -> bool:
    """Return True when the path is under a Homebrew prefix."""
    if "cellar" in parts_lower or "linuxbrew" in parts_lower:
        return True
    markers = (
        "/opt/homebrew/",
        "/home/linuxbrew/",
        "/usr/local/homebrew/",
        "/homebrew/",
    )
    return any(marker in path_lower for marker in markers)


def _is_uv_tool_path(*, resolved: Path, path_lower: str) -> bool:
    """Return True when the path is under a uv tools directory."""
    if "/uv/tools/" in path_lower or path_lower.endswith("/uv/tools"):
        return True
    uv_tool_dir = os.environ.get("UV_TOOL_DIR")
    if uv_tool_dir:
        try:
            return resolved.is_relative_to(Path(uv_tool_dir).expanduser().resolve())
        except (OSError, ValueError, RuntimeError):
            return False
    return False


def _is_cargo_path(*, path_lower: str) -> bool:
    """Return True when the path is under cargo's bin directory."""
    if "/.cargo/bin/" in path_lower or path_lower.rstrip("/").endswith("/.cargo/bin"):
        return True
    cargo_home = os.environ.get("CARGO_HOME")
    if cargo_home:
        cargo_bin = f"{cargo_home.rstrip('/').lower()}/bin/"
        return cargo_bin in path_lower or path_lower.rstrip("/").endswith(
            cargo_bin.rstrip("/"),
        )
    return False


def _is_rustup_path(*, path_lower: str, parts_lower: set[str]) -> bool:
    """Return True when the path is under a rustup toolchain tree."""
    if "rustup" in parts_lower:
        return True
    return "/toolchains/" in path_lower and (
        "rust" in path_lower or "clippy" in path_lower or "rustc" in path_lower
    )


def _is_bun_path(*, path_lower: str, parts_lower: set[str]) -> bool:
    """Return True when the path is under a Bun install prefix."""
    if ".bun" in parts_lower:
        return True
    bun_install = os.environ.get("BUN_INSTALL")
    if bun_install:
        return bun_install.rstrip("/").lower() in path_lower
    return "/.bun/" in path_lower


def _is_pip_path(*, path_lower: str, parts_lower: set[str]) -> bool:
    """Return True when the path looks like a pip/venv install."""
    markers = (
        "site-packages",
        "dist-packages",
        ".venv",
        "virtualenv",
    )
    if any(marker in path_lower for marker in markers):
        return True
    if "venv" in parts_lower:
        return True
    # python -m / Scripts on Windows
    if sys.platform == "win32" and "scripts" in parts_lower:
        return True
    return False


def _is_standalone_path(*, path_lower: str) -> bool:
    """Return True for common system bin prefixes without a known manager."""
    prefixes = (
        "/usr/local/bin/",
        "/usr/bin/",
        "/bin/",
    )
    return any(path_lower.startswith(prefix) for prefix in prefixes)


def _package_for_channel(
    *,
    channel: UpdateChannel,
    tool_name: str,
    install_package: str | None,
) -> str:
    """Choose the package name used in the update command."""
    if install_package:
        return install_package
    if channel == UpdateChannel.HOMEBREW:
        return BREW_FORMULA_NAMES.get(tool_name, tool_name.replace("_", "-"))
    if channel in (UpdateChannel.CARGO, UpdateChannel.NPM, UpdateChannel.BUN):
        return tool_name.replace("_", "-")
    return tool_name
