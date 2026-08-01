"""Tests for the ai-tools Dockerfile ARG pin resolver.

The AI review dogfood installs the ``claude`` CLI on a bare runner, so the
version it installs has to come from the same Renovate-managed pin the released
``ai`` image is built from. These tests cover the resolver that reads it: a
missing or unpinned ARG must fail loudly rather than let the workflow fall back
to an unreviewed ``latest``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "ai_tools_arg_pin.py"
DOCKERFILE = REPO_ROOT / "docker" / "ai-tools.Dockerfile"

DOCKERFILE_SAMPLE = """\
FROM scratch AS ai-tools
ARG NODE_VERSION=24.18.0
ARG CLAUDE_CODE_VERSION=2.1.220
ARG UNPINNED=
ARG MOVING_TAG=latest
ARG MOVING_RANGE=^2.1.220
"""


@pytest.fixture
def pin_module() -> ModuleType:
    """Load ``ai_tools_arg_pin.py`` as an importable module.

    Returns:
        The loaded module exposing its public helpers.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location("ai_tools_arg_pin", SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ai_tools_arg_pin"] = module
    spec.loader.exec_module(module)
    return module


def test_resolve_arg_returns_pinned_value(*, pin_module: ModuleType) -> None:
    """A pinned ARG resolves to its literal default value.

    Args:
        pin_module: The loaded resolver module.
    """
    value = pin_module.resolve_arg(
        dockerfile_text=DOCKERFILE_SAMPLE,
        name="CLAUDE_CODE_VERSION",
    )

    assert_that(value).is_equal_to("2.1.220")


def test_resolve_arg_rejects_missing_arg(*, pin_module: ModuleType) -> None:
    """An absent ARG raises rather than resolving to an empty string.

    Silently returning nothing would let the caller install an unpinned CLI
    into a job that holds a credential.

    Args:
        pin_module: The loaded resolver module.
    """
    assert_that(pin_module.resolve_arg).raises(ValueError).when_called_with(
        dockerfile_text=DOCKERFILE_SAMPLE,
        name="NOT_A_REAL_ARG",
    )


def test_resolve_arg_rejects_valueless_arg(*, pin_module: ModuleType) -> None:
    """An ARG declared without a default value is treated as unpinned.

    Args:
        pin_module: The loaded resolver module.
    """
    assert_that(pin_module.resolve_arg).raises(ValueError).when_called_with(
        dockerfile_text=DOCKERFILE_SAMPLE,
        name="UNPINNED",
    )


def test_format_line_renders_github_output_keys(*, pin_module: ModuleType) -> None:
    """GitHub format emits lowercase, dash-separated ``key=value`` lines.

    Args:
        pin_module: The loaded resolver module.
    """
    line = pin_module.format_line(
        name="CLAUDE_CODE_VERSION",
        value="2.1.220",
        output_format="github",
    )

    assert_that(line).is_equal_to("claude-code-version=2.1.220")


def test_main_prints_requested_pins_in_order(
    *,
    pin_module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() prints one value per requested ARG, in the requested order.

    Args:
        pin_module: The loaded resolver module.
        tmp_path: Temporary directory holding the fixture Dockerfile.
        capsys: Capture fixture for stdout.
    """
    dockerfile = tmp_path / "ai-tools.Dockerfile"
    dockerfile.write_text(DOCKERFILE_SAMPLE, encoding="utf-8")

    exit_code = pin_module.main(
        argv=[
            "CLAUDE_CODE_VERSION",
            "NODE_VERSION",
            "--dockerfile",
            str(dockerfile),
        ],
    )

    assert_that(exit_code).is_equal_to(0)
    assert_that(capsys.readouterr().out.split()).is_equal_to(["2.1.220", "24.18.0"])


def test_main_reports_missing_dockerfile(
    *,
    pin_module: ModuleType,
    tmp_path: Path,
) -> None:
    """A missing Dockerfile exits non-zero instead of raising.

    Args:
        pin_module: The loaded resolver module.
        tmp_path: Temporary directory that intentionally holds no Dockerfile.
    """
    exit_code = pin_module.main(
        argv=["NODE_VERSION", "--dockerfile", str(tmp_path / "missing.Dockerfile")],
    )

    assert_that(exit_code).is_equal_to(1)


def test_real_dockerfile_pins_the_versions_ci_installs(
    *,
    pin_module: ModuleType,
) -> None:
    """The committed ai-tools Dockerfile still carries both pins CI reads.

    The dogfood workflow resolves these two ARGs by name; renaming or dropping
    either would break the review job at install time rather than here.

    Args:
        pin_module: The loaded resolver module.
    """
    dockerfile_text = DOCKERFILE.read_text(encoding="utf-8")

    for name in ("NODE_VERSION", "CLAUDE_CODE_VERSION"):
        assert_that(
            pin_module.resolve_arg(dockerfile_text=dockerfile_text, name=name),
        ).described_as(name).is_not_empty()


@pytest.mark.parametrize("name", ["MOVING_TAG", "MOVING_RANGE"])
def test_exact_mode_rejects_moving_versions(
    *,
    pin_module: ModuleType,
    name: str,
) -> None:
    """``--exact`` refuses dist-tags and ranges, which are non-empty but move.

    Args:
        pin_module: The loaded resolver module.
        name: Build-argument name carrying a non-exact value.
    """
    assert_that(pin_module.resolve_arg).raises(ValueError).when_called_with(
        dockerfile_text=DOCKERFILE_SAMPLE,
        name=name,
        exact=True,
    )


def test_exact_mode_accepts_a_literal_version(*, pin_module: ModuleType) -> None:
    """``--exact`` passes a literal X.Y.Z version through unchanged.

    Args:
        pin_module: The loaded resolver module.
    """
    value = pin_module.resolve_arg(
        dockerfile_text=DOCKERFILE_SAMPLE,
        name="CLAUDE_CODE_VERSION",
        exact=True,
    )

    assert_that(value).is_equal_to("2.1.220")


def test_main_exits_non_zero_on_a_moving_pin(
    *,
    pin_module: ModuleType,
    tmp_path: Path,
) -> None:
    """A moving pin fails the resolving step instead of reaching npm.

    Args:
        pin_module: The loaded resolver module.
        tmp_path: Temporary directory holding the fixture Dockerfile.
    """
    dockerfile = tmp_path / "ai-tools.Dockerfile"
    dockerfile.write_text(DOCKERFILE_SAMPLE, encoding="utf-8")

    exit_code = pin_module.main(
        argv=["MOVING_TAG", "--exact", "--dockerfile", str(dockerfile)],
    )

    assert_that(exit_code).is_equal_to(1)


def test_real_dockerfile_pins_are_exact_versions(*, pin_module: ModuleType) -> None:
    """The committed pins CI installs are literal versions, not ranges.

    Args:
        pin_module: The loaded resolver module.
    """
    dockerfile_text = DOCKERFILE.read_text(encoding="utf-8")

    for name in ("NODE_VERSION", "CLAUDE_CODE_VERSION"):
        assert_that(
            pin_module.resolve_arg(
                dockerfile_text=dockerfile_text,
                name=name,
                exact=True,
            ),
        ).described_as(name).matches(r"^\d+\.\d+\.\d+$")
