"""Unit tests for update channel detection and version advisories."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core import update_channels
from lintro.tools.core.update_channels import (
    VersionAdvisory,
    build_version_advisory,
    detect_update_channel,
    format_advisory_line,
    resolve_update_command,
)
from lintro.tools.core.version_checking import (
    build_version_advisory as build_outdated_advisory,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "/opt/homebrew/Cellar/ruff/0.9.0/bin/ruff",
            UpdateChannel.HOMEBREW,
        ),
        (
            "/home/linuxbrew/.linuxbrew/Cellar/shellcheck/0.11.0/bin/shellcheck",
            UpdateChannel.HOMEBREW,
        ),
        (
            "/opt/homebrew/Cellar/prettier/3.9.5/libexec/lib/node_modules/"
            "prettier/bin/prettier.cjs",
            UpdateChannel.HOMEBREW,
        ),
        (
            "/Users/me/.local/share/uv/tools/ruff/bin/ruff",
            UpdateChannel.UV_TOOL,
        ),
        (
            "/Users/me/.cargo/bin/taplo",
            UpdateChannel.CARGO,
        ),
        (
            "/Users/me/.rustup/toolchains/stable-aarch64-apple-darwin/bin/rustc",
            UpdateChannel.RUSTUP,
        ),
        (
            "/Users/me/.bun/bin/prettier",
            UpdateChannel.BUN,
        ),
        (
            "/proj/node_modules/.bin/prettier",
            UpdateChannel.NPM,
        ),
        (
            "/proj/.venv/bin/ruff",
            UpdateChannel.PIP,
        ),
        (
            "/usr/lib/python3.12/site-packages/bin/yamllint",
            UpdateChannel.PIP,
        ),
        (
            "/usr/bin/hadolint-lintro-fake-standalone",
            UpdateChannel.STANDALONE,
        ),
        (
            "/opt/mystery/bin/tool",
            UpdateChannel.UNKNOWN,
        ),
        (
            None,
            UpdateChannel.UNKNOWN,
        ),
    ],
    ids=[
        "homebrew-cellar",
        "linuxbrew",
        "homebrew-npm-formula",
        "uv-tool",
        "cargo",
        "rustup",
        "bun",
        "npm-local",
        "venv",
        "site-packages",
        "standalone",
        "unknown",
        "missing-path",
    ],
)
def test_detect_update_channel(
    path: str | None,
    expected: UpdateChannel,
) -> None:
    """Classify install channels from representative binary paths."""
    assert_that(detect_update_channel(path)).is_equal_to(expected)


def test_detect_update_channel_homebrew_bin_prefix() -> None:
    """Binaries under the Homebrew bin prefix resolve as homebrew."""
    # On Homebrew Macs /usr/local/bin often symlinks to /opt/homebrew/bin.
    channel = detect_update_channel("/opt/homebrew/bin/some-tool")
    assert_that(channel).is_equal_to(UpdateChannel.HOMEBREW)


def test_detect_update_channel_respects_override() -> None:
    """Explicit channel overrides beat path heuristics."""
    channel = detect_update_channel(
        "/opt/homebrew/Cellar/ruff/0.9.0/bin/ruff",
        channel_override=UpdateChannel.UV_TOOL,
    )
    assert_that(channel).is_equal_to(UpdateChannel.UV_TOOL)


def test_detect_update_channel_tool_override_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-tool override table wins when heuristics would otherwise apply."""
    monkeypatch.setitem(
        update_channels.TOOL_CHANNEL_OVERRIDES,
        "ruff",
        UpdateChannel.PIP,
    )
    channel = detect_update_channel(
        "/opt/homebrew/Cellar/ruff/0.9.0/bin/ruff",
        tool_name="ruff",
    )
    assert_that(channel).is_equal_to(UpdateChannel.PIP)


def test_detect_update_channel_uv_tool_dir_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """UV_TOOL_DIR environment variable is honored for channel detection."""
    tool_bin = tmp_path / "custom-uv-tools" / "ruff" / "bin" / "ruff"
    tool_bin.parent.mkdir(parents=True)
    tool_bin.write_text("#!/bin/sh\n")
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "custom-uv-tools"))
    assert_that(detect_update_channel(tool_bin)).is_equal_to(UpdateChannel.UV_TOOL)


@pytest.mark.parametrize(
    ("channel", "tool", "package", "latest", "expected"),
    [
        (
            UpdateChannel.HOMEBREW,
            "hadolint",
            None,
            "2.12.0",
            "brew upgrade hadolint",
        ),
        (
            UpdateChannel.HOMEBREW,
            "osv_scanner",
            None,
            "2.4.0",
            "brew upgrade osv-scanner",
        ),
        (
            UpdateChannel.UV_TOOL,
            "ruff",
            None,
            "0.9.0",
            "uv tool upgrade ruff",
        ),
        (
            UpdateChannel.PIP,
            "ruff",
            "ruff",
            "0.9.0",
            "uv pip install --upgrade 'ruff>=0.9.0'",
        ),
        (
            UpdateChannel.NPM,
            "prettier",
            "prettier",
            "3.9.4",
            "npm install -g prettier@3.9.4",
        ),
        (
            UpdateChannel.BUN,
            "prettier",
            "prettier",
            "3.9.4",
            "bun add -g prettier@3.9.4",
        ),
        (
            UpdateChannel.CARGO,
            "cargo_audit",
            "cargo-audit",
            "0.22.0",
            "cargo install --force cargo-audit",
        ),
        (
            UpdateChannel.RUSTUP,
            "clippy",
            None,
            "1.97.1",
            "rustup update stable",
        ),
        (
            UpdateChannel.UNKNOWN,
            "hadolint",
            None,
            "2.12.0",
            None,
        ),
        (
            UpdateChannel.STANDALONE,
            "hadolint",
            None,
            "2.12.0",
            None,
        ),
    ],
    ids=[
        "brew",
        "brew-formula-alias",
        "uv-tool",
        "pip",
        "npm",
        "bun",
        "cargo",
        "rustup",
        "unknown",
        "standalone",
    ],
)
def test_resolve_update_command(
    channel: UpdateChannel,
    tool: str,
    package: str | None,
    latest: str,
    expected: str | None,
) -> None:
    """Map channels to update command templates."""
    command = resolve_update_command(
        channel=channel,
        tool_name=tool,
        install_package=package,
        latest_known=latest,
    )
    assert_that(command).is_equal_to(expected)


def test_build_version_advisory_includes_command() -> None:
    """Advisory carries channel and update command for a known path."""
    advisory = build_version_advisory(
        tool="ruff",
        installed="0.6.9",
        latest_known="0.9.0",
        binary_path="/Users/me/.local/share/uv/tools/ruff/bin/ruff",
    )
    assert_that(advisory.channel).is_equal_to(UpdateChannel.UV_TOOL)
    assert_that(advisory.update_command).is_equal_to("uv tool upgrade ruff")
    assert_that(advisory.to_dict()).contains_key("channel", "update_command")


def test_build_outdated_advisory_returns_none_when_current() -> None:
    """version_checking.build_version_advisory skips up-to-date tools."""
    advisory = build_outdated_advisory(
        tool="ruff",
        installed="0.9.0",
        latest_known="0.9.0",
        binary_path="/opt/homebrew/Cellar/ruff/0.9.0/bin/ruff",
    )
    assert_that(advisory).is_none()


def test_build_outdated_advisory_falls_back_to_install_type() -> None:
    """Unknown paths fall back to manifest install.type for a command."""
    advisory = build_outdated_advisory(
        tool="ruff",
        installed="0.6.9",
        latest_known="0.9.0",
        binary_path="/opt/mystery/bin/ruff",
        install_type="pip",
        install_package="ruff",
    )
    assert_that(advisory).is_not_none()
    assert advisory is not None
    assert_that(advisory.channel).is_equal_to(UpdateChannel.PIP)
    assert_that(advisory.update_command).contains("uv pip install --upgrade")


def test_format_advisory_line_with_command() -> None:
    """Human-readable line includes channel and update command."""
    advisory = VersionAdvisory(
        tool="hadolint",
        installed="2.10",
        latest_known="2.12",
        channel=UpdateChannel.HOMEBREW,
        update_command="brew upgrade hadolint",
    )
    line = format_advisory_line(advisory)
    assert_that(line).contains("hadolint 2.10 installed")
    assert_that(line).contains("2.12 expected")
    assert_that(line).contains("installed via homebrew")
    assert_that(line).contains("brew upgrade hadolint")


def test_format_advisory_line_unknown_channel() -> None:
    """Unknown channel degrades without inventing an update command."""
    advisory = VersionAdvisory(
        tool="hadolint",
        installed="2.10",
        latest_known="2.12",
        channel=UpdateChannel.UNKNOWN,
        update_command=None,
    )
    line = format_advisory_line(advisory)
    assert_that(line).contains("update channel unknown")
    assert_that(line).does_not_contain("brew")
