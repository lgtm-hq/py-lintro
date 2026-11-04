"""Exercise the native Clippy module's actual collection prerequisite."""

from __future__ import annotations

import runpy
import subprocess  # nosec B404 - completed version-probe fixtures only
from collections.abc import Sequence
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_min_version
from lintro.enums.tool_name import ToolName
from tests.integration import _tools

_MODULE = (
    Path(__file__).parents[1] / "integration" / "tools" / "test_clippy_accounting.py"
)


@pytest.mark.parametrize("image", [False, True], ids=["local", "tools-image"])
@pytest.mark.parametrize(
    ("supported", "allow_lag", "skip"),
    [(False, False, True), (True, False, False), (False, True, False)],
    ids=["old-clippy-new-rustc", "supported-clippy", "allowed-artifact-lag"],
)
def test_native_module_gates_on_clippy_version(
    monkeypatch: pytest.MonkeyPatch,
    image: bool,
    supported: bool,
    allow_lag: bool,
    skip: bool,
) -> None:
    """A supported rustc cannot mask old Clippy, including in the tools image."""
    floor = get_min_version(ToolName.CLIPPY)
    banner = f"clippy {floor}" if supported else "clippy 0.1.0"
    monkeypatch.setenv(_tools.TOOLS_IMAGE_ENV, "1" if image else "0")
    monkeypatch.setenv(_tools.ALLOW_VERSION_LAG_ENV, "clippy" if allow_lag else "")
    monkeypatch.setattr(_tools, "_which", lambda name: f"/tools/{name}")

    def probe(
        command: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Return distinct version identities for rustc and cargo clippy."""
        output = f"rustc {floor}" if command[0] == "rustc" else banner
        return subprocess.CompletedProcess(list(command), 0, output, "")

    monkeypatch.setattr(_tools, "_run", probe)
    marks = runpy.run_path(str(_MODULE))["pytestmark"]

    assert_that(any(mark.args[0] for mark in marks)).is_equal_to(skip)
    if skip:
        reasons = " ".join(mark.kwargs["reason"] for mark in marks)
        assert_that(reasons).contains("clippy", floor, "found: 1.0.0")


@pytest.mark.parametrize("missing", [False, True], ids=["unrunnable", "absent"])
def test_native_module_requires_runnable_clippy_in_image(
    monkeypatch: pytest.MonkeyPatch,
    missing: bool,
) -> None:
    """Neither a missing component nor a failed probe may skip the tools image."""
    monkeypatch.setenv(_tools.TOOLS_IMAGE_ENV, "1")
    monkeypatch.setattr(
        _tools,
        "_which",
        lambda name: None if name == "cargo" and missing else f"/tools/{name}",
    )

    def probe(
        command: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Rustc works even when its sibling Clippy component does not."""
        return subprocess.CompletedProcess(
            list(command),
            int(command[0] == "cargo"),
            "rustc 99.0.0",
            "",
        )

    monkeypatch.setattr(_tools, "_run", probe)

    with pytest.raises(_tools.MissingToolError, match="not installed or not runnable"):
        runpy.run_path(str(_MODULE))
