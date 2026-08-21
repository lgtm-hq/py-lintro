"""Resolve per-tool update channels and version advisories.

Given a tool binary path, detect how the tool was installed (Homebrew, uv
tool, pip/venv, npm/bun, cargo, rustup, standalone) and map that channel to
an actionable update command. "Latest known" versions come from pinned
manifest / tool-versions data — this module never makes network calls.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core.install_strategies.package_names import (
    brew_formula_name,
    ecosystem_package_name,
)

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
    are classified correctly. The in-code ``TOOL_CHANNEL_OVERRIDES`` table
    wins when heuristics are known-wrong. Manifest ``channel_override`` is a
    fallback used only when path detection is ``UNKNOWN`` or ``STANDALONE``.

    Args:
        binary_path: Absolute or relative path to the tool binary.
        tool_name: Canonical tool name used for override lookup.
        channel_override: Explicit channel from the manifest; applied when
            path heuristics do not identify a manager.

    Returns:
        Detected :class:`UpdateChannel`. Unknown paths degrade to
        ``UNKNOWN`` (or ``STANDALONE`` for common system bin prefixes).
    """
    if tool_name:
        mapped = TOOL_CHANNEL_OVERRIDES.get(tool_name)
        if mapped is not None:
            return mapped

    if not binary_path:
        override = _coerce_channel(channel_override)
        return override if override is not None else UpdateChannel.UNKNOWN

    resolved = _resolve_binary_path(binary_path)
    if resolved is None:
        return UpdateChannel.UNKNOWN

    path_lower = resolved.as_posix().lower()
    parts_lower = {part.lower() for part in resolved.parts}

    # Homebrew before node_modules: brew formulae for JS tools live under
    # Cellar/.../libexec/lib/node_modules/...
    if _is_homebrew_path(path_lower=path_lower):
        return UpdateChannel.HOMEBREW

    if _is_uv_tool_path(resolved=resolved, path_lower=path_lower):
        return UpdateChannel.UV_TOOL

    if _is_cargo_path(resolved=resolved, path_lower=path_lower):
        # rustup installs proxy shims in ``~/.cargo/bin`` (rustc, cargo,
        # rustfmt, clippy). Those are not cargo-installed crates.
        if _is_rustup_shim(resolved=resolved, tool_name=tool_name):
            return UpdateChannel.RUSTUP
        return UpdateChannel.CARGO

    if _is_rustup_path(path_lower=path_lower, parts_lower=parts_lower):
        return UpdateChannel.RUSTUP

    if _is_bun_path(resolved=resolved, path_lower=path_lower, parts_lower=parts_lower):
        return UpdateChannel.BUN

    if "node_modules" in parts_lower:
        return UpdateChannel.NPM

    if _is_pip_path(path_lower=path_lower, parts_lower=parts_lower):
        return UpdateChannel.PIP

    if _is_standalone_path(path_lower=path_lower):
        detected = UpdateChannel.STANDALONE
    else:
        detected = UpdateChannel.UNKNOWN

    override = _coerce_channel(channel_override)
    if override is not None and detected in (
        UpdateChannel.UNKNOWN,
        UpdateChannel.STANDALONE,
    ):
        return override
    return detected


def resolve_update_command(
    *,
    channel: UpdateChannel,
    tool_name: str,
    install_package: str | None = None,
    latest_known: str | None = None,
    binary_path: str | Path | None = None,
) -> str | None:
    """Map an install channel to an actionable update command.

    Args:
        channel: Detected (or overridden) install channel.
        tool_name: Canonical tool name.
        install_package: Package name override from the manifest.
        latest_known: Pinned expected version for channels that pin on upgrade.
        binary_path: Installed binary path. Project-local ``node_modules``
            installs must not emit a global ``npm install -g`` / ``bun add -g``.

    Returns:
        Shell command string, or None when the channel has no known template.
    """
    package = _package_for_channel(
        channel=channel,
        tool_name=tool_name,
        install_package=install_package,
    )
    version = latest_known or ""
    project_local_node = _is_project_local_node_path(binary_path)

    if channel == UpdateChannel.HOMEBREW:
        return f"brew upgrade {package}"
    if channel == UpdateChannel.UV_TOOL:
        return f"uv tool upgrade {package}"
    if channel == UpdateChannel.PIP:
        if tool_name == "semgrep":
            return None
        pip_prefix = "uv pip install" if shutil.which("uv") else "pip install"
        if version:
            return f"{pip_prefix} --upgrade '{package}>={version}'"
        return f"{pip_prefix} --upgrade {package}"
    if channel == UpdateChannel.NPM:
        spec = f"{package}@{version}" if version else package
        if project_local_node:
            return f"npm install -D {spec}"
        return f"npm install -g {spec}"
    if channel == UpdateChannel.BUN:
        spec = f"{package}@{version}" if version else package
        if project_local_node:
            return f"bun add -D {spec}"
        return f"bun add -g {spec}"
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
        binary_path=binary_path,
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

    Channel is diagnostic only; the actionable upgrade command lives on
    the install strategy ``upgrade_hint`` / ``install_hint``.

    Args:
        advisory: Structured advisory to format.

    Returns:
        Single-line advisory matching doctor / versions output style.
    """
    base = (
        f"{advisory.tool} {advisory.installed} installed, "
        f"{advisory.latest_known} expected"
    )
    if advisory.channel == UpdateChannel.UNKNOWN:
        return f"{base} — update channel unknown"
    if advisory.channel == UpdateChannel.STANDALONE:
        return f"{base} — installed as a standalone binary"
    channel_label = advisory.channel.value.replace("_", " ")
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


def _is_homebrew_path(*, path_lower: str) -> bool:
    """Return True when the path is under a Homebrew prefix.

    Intel Homebrew often links from ``/usr/local/bin``. That prefix is shared
    with many non-brew installs, so it is classified as ``STANDALONE`` unless
    symlink resolution reaches Cellar or ``/usr/local/Homebrew``. Apple
    Silicon ``/opt/homebrew/`` is a dedicated prefix and matches directly.
    """
    markers = (
        "/opt/homebrew/",
        "/home/linuxbrew/",
        "/usr/local/homebrew/",
        "/usr/local/cellar/",
        "/opt/homebrew/cellar/",
        "/.linuxbrew/",
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


_RUSTUP_SHIM_NAMES: frozenset[str] = frozenset(
    {
        "rustc",
        "cargo",
        "rustfmt",
        "clippy",
        "cargo-clippy",
    },
)

#: Version-probe argv[0] values that are host wrappers, not the tool binary.
_WRAPPER_PROBE_NAMES: frozenset[str] = frozenset({"sh", "bash", "cargo", "env"})


def resolve_channel_binary_path(
    *,
    tool_name: str,
    install_bin: str | None = None,
    probe_path: str | Path | None = None,
    probe_argv0: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> str | Path | None:
    """Return the binary path used for update-channel detection.

    Cargo crates and some Node tools probe version through ``cargo`` or
    ``bash``. Those argv[0] paths are not the tool and must not classify
    the install channel.

    Args:
        tool_name: Canonical tool name.
        install_bin: Manifest ``install.bin`` when it differs from the name.
        probe_path: Path resolved from the version-probe argv[0].
        probe_argv0: Version-probe argv[0] (may be a relative command name).
        which: PATH lookup, defaulting to :func:`shutil.which`.

    Returns:
        Path of the tool binary when it can be found, otherwise *probe_path*.
    """
    argv0_source = probe_argv0 if probe_argv0 is not None else probe_path
    argv0 = ""
    if argv0_source is not None:
        argv0 = Path(argv0_source).name.lower().removesuffix(".exe")
    if argv0 not in _WRAPPER_PROBE_NAMES:
        return probe_path

    finder = which or shutil.which
    candidates: list[str] = []
    if install_bin:
        candidates.append(install_bin)
    hyphen = tool_name.replace("_", "-")
    underscore = tool_name.replace("-", "_")
    for name in (hyphen, underscore, tool_name):
        if name not in candidates:
            candidates.append(name)
    for name in candidates:
        found = finder(name)
        if found:
            return found
    return probe_path


def _is_rustup_shim(*, resolved: Path, tool_name: str | None) -> bool:
    """Return True for rustup proxy binaries that live under ``~/.cargo/bin``.

    When *tool_name* is set, only that name (and hyphen/underscore aliases)
    is matched. Wrapper probes such as ``cargo audit`` resolve argv[0] to
    the ``cargo`` shim; classifying from the basename would label
    cargo-audit as rustup.
    """
    if tool_name:
        lowered = tool_name.lower()
        names = {
            lowered,
            lowered.replace("-", "_"),
            lowered.replace("_", "-"),
        }
    else:
        names = {resolved.name.lower().removesuffix(".exe")}
    return bool(names & _RUSTUP_SHIM_NAMES)


def _is_project_local_node_path(binary_path: str | Path | None) -> bool:
    """Return True when the binary is a project-local ``node_modules`` install."""
    if binary_path is None:
        return False
    try:
        parts = {part.lower() for part in Path(binary_path).parts}
    except (OSError, ValueError, RuntimeError):
        return False
    return "node_modules" in parts


def _is_cargo_path(*, resolved: Path, path_lower: str) -> bool:
    """Return True when the path is under cargo's bin directory."""
    if "/.cargo/bin/" in path_lower or path_lower.rstrip("/").endswith("/.cargo/bin"):
        return True
    cargo_home = os.environ.get("CARGO_HOME")
    if cargo_home:
        try:
            return resolved.is_relative_to(
                Path(cargo_home).expanduser().resolve() / "bin",
            )
        except (OSError, ValueError, RuntimeError):
            return False
    return False


def _is_rustup_path(*, path_lower: str, parts_lower: set[str]) -> bool:
    """Return True when the path is under a rustup toolchain tree."""
    if ".rustup" in parts_lower:
        return True
    return "/.rustup/" in path_lower


def _is_bun_path(
    *,
    resolved: Path,
    path_lower: str,
    parts_lower: set[str],
) -> bool:
    """Return True when the path is under a Bun install prefix."""
    if ".bun" in parts_lower:
        return True
    bun_install = os.environ.get("BUN_INSTALL")
    if bun_install:
        try:
            return resolved.is_relative_to(Path(bun_install).expanduser().resolve())
        except (OSError, ValueError, RuntimeError):
            return False
    return "/.bun/" in path_lower


def _is_pip_path(*, path_lower: str, parts_lower: set[str]) -> bool:
    """Return True when the path looks like a pip/venv install."""
    if "/.venv/bin/" in path_lower or path_lower.rstrip("/").endswith("/.venv/bin"):
        return True
    if "site-packages" in parts_lower and "bin" in parts_lower:
        return True
    if "dist-packages" in parts_lower and "bin" in parts_lower:
        return True
    if "venv" in parts_lower and "bin" in parts_lower:
        return True
    if "virtualenv" in parts_lower and "bin" in parts_lower:
        return True
    # python -m / Scripts on Windows
    if sys.platform == "win32" and "scripts" in parts_lower:
        return True
    return False


def _is_standalone_path(*, path_lower: str) -> bool:
    """Return True for common system bin prefixes without a known manager.

    ``/usr/local/bin`` is intentionally standalone when Homebrew detection
    did not already match. Treating the whole prefix as Homebrew would
    mislabel non-brew binaries on Intel Macs and Linux.
    """
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
    if channel == UpdateChannel.HOMEBREW:
        return brew_formula_name(
            tool_name=tool_name,
            install_package=install_package,
        )
    return ecosystem_package_name(
        tool_name=tool_name,
        install_package=install_package,
    )
