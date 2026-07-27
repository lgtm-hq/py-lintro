"""Contract tests for the baked AI agent CLI image.

The value these guard is drift between three places that must agree but have
no runtime link: the binaries lintro's CLI transports look up
(``lintro/ai/providers/cli_contracts.py``), the binaries the ai-tools image
actually bakes (``scripts/utils/install-ai-tools.sh``), and the build/publish
wiring that ships them.

The root Dockerfile's ``ai`` stage and its publish job land in the follow-up
that pins this image's digest, so nothing here asserts on them yet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from assertpy import assert_that

from lintro.ai.providers.cli_contracts import CLI_CONTRACTS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AI_TOOLS_DOCKERFILE = _REPO_ROOT / "docker" / "ai-tools.Dockerfile"
_INSTALLER = _REPO_ROOT / "scripts" / "utils" / "install-ai-tools.sh"
_RENOVATE = _REPO_ROOT / "renovate.json"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

# Cursor ships the agent CLI only as a tarball behind https://cursor.com/install
# — no registry, no release feed, and no checksum sidecar — so there is nothing
# for Renovate to query and the version and its two hashes move by hand.
_ARGS_WITHOUT_RENOVATE_DATASOURCE = frozenset(
    {
        "CURSOR_AGENT_VERSION",
        "CURSOR_AGENT_SHA256_X64",
        "CURSOR_AGENT_SHA256_ARM64",
    },
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_workflow(*, name: str) -> dict[str, Any]:
    data = yaml.safe_load(_read(_WORKFLOWS / name))
    assert_that(data).is_instance_of(dict)
    return cast(dict[str, Any], data)


def _dockerfile_args(text: str) -> set[str]:
    return set(re.findall(r"^ARG (\w+)=", text, flags=re.MULTILINE))


def _contract_binaries() -> list[str]:
    return sorted({contract.binary for contract in CLI_CONTRACTS.values()})


@pytest.mark.parametrize("binary", _contract_binaries())
def test_installer_bakes_every_declared_cli_binary(binary: str) -> None:
    """Every binary a CLI transport looks up gets a shim in the image.

    Args:
        binary: Executable name declared by a provider's CLI contract.
    """
    installer = _read(_INSTALLER)

    assert_that(installer).contains(f'write_shim "{binary}"')


@pytest.mark.parametrize("binary", _contract_binaries())
def test_installer_verifies_every_declared_cli_binary(binary: str) -> None:
    """A baked binary that cannot run fails the image build, not a review.

    Args:
        binary: Executable name declared by a provider's CLI contract.
    """
    installer = _read(_INSTALLER)

    assert_that(installer).contains(f"{binary} --version")


def test_ai_tools_image_builds_on_the_pinned_tools_base() -> None:
    """The AI image extends lintro-tools by immutable digest."""
    from_lines = [
        line
        for line in _read(_AI_TOOLS_DOCKERFILE).splitlines()
        if line.startswith("FROM ")
    ]

    assert_that(from_lines).is_length(1)
    assert_that(from_lines[0]).matches(
        r"^FROM ghcr\.io/lgtm-hq/lintro-tools:latest@sha256:[a-f0-9]{64} AS ",
    )


def test_ai_tools_dockerfile_passes_every_arg_to_the_installer() -> None:
    """Every declared version ARG reaches the installer that consumes it."""
    dockerfile = _read(_AI_TOOLS_DOCKERFILE)

    for arg in _dockerfile_args(dockerfile):
        assert_that(dockerfile).contains(f'{arg}="${{{arg}}}"')


def test_renovate_tracks_every_trackable_ai_tools_arg() -> None:
    """Baked CLI versions are Renovate-managed except the documented opt-out."""
    renovate = json.loads(_read(_RENOVATE))
    ai_tools_managers = [
        manager
        for manager in renovate["customManagers"]
        if "docker/ai-tools.Dockerfile" in manager["managerFilePatterns"]
    ]
    tracked = {
        match.group(1)
        for manager in ai_tools_managers
        for pattern in manager["matchStrings"]
        if (match := re.search(r"ARG (\w+)=", pattern))
    }

    declared = _dockerfile_args(_read(_AI_TOOLS_DOCKERFILE))
    assert_that(declared - tracked).is_equal_to(set(_ARGS_WITHOUT_RENOVATE_DATASOURCE))


def test_ai_tools_publish_workflow_rebuilds_on_installer_changes() -> None:
    """Editing the installer or its smoke test rebuilds the image."""
    workflow = _load_workflow(name="docker-ai-tools-publish.yml")
    triggers = workflow["on"]

    for event in ("push", "pull_request"):
        assert_that(triggers[event]["paths"]).contains(
            "docker/ai-tools.Dockerfile",
            "scripts/utils/install-ai-tools.sh",
            "scripts/ci/smoke-test-ai-tools.sh",
        )
