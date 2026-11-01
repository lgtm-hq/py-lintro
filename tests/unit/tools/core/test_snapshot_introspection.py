"""Keep introspection offline without restricting execution's Node fallback."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - fake subprocess boundary; nothing is executed
import sys
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.list_tools import list_tools_command
from lintro.config.lintro_config import LintroConfig
from lintro.enums.tool_status import ToolStatus
from lintro.mcp.toolkits.introspection import build_introspection_toolkit
from lintro.tools import tool_manager
from lintro.tools.core import command_builders, snapshots
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.tool_registry import ManifestRegistry
from lintro.utils import doctor_report

_TOOLS = ["stylelint", "prettier", "shellcheck", "ruff"]


@pytest.fixture(params=["bunx", "npx"])
def probe_commands(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[list[str]]:
    """Use real builders and parsers behind a fake, recording subprocess boundary."""
    commands: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="99.0.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    # Plain executable stand-ins suffice; never create node_modules or fetch packages.
    binaries = {}
    for name in ["prettier", "shellcheck", "ruff", request.param]:
        binary = tmp_path / name
        binary.write_text("#!/bin/sh\nexit 99\n")
        binary.chmod(0o755)
        binaries[name] = str(binary)
    real_which = shutil.which

    def which(command: str, path: str | None = None) -> str | None:
        if Path(command).is_absolute():
            return real_which(command, path=path)
        return binaries.get(command) if command != "prettier" else None

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(
        command_builders,
        "find_local_node_binary",
        lambda name, *, start=None: (
            binaries["prettier"] if name == "prettier" else None
        ),
    )
    plugins = {name: tool_manager.get_tool(name) for name in _TOOLS}
    monkeypatch.setattr(tool_manager, "get_all_tools", lambda: plugins)
    manifest = ManifestRegistry.load()
    manifest = ManifestRegistry(
        tools={name: manifest.get(name) for name in _TOOLS},
        language_map={},
        profiles={},
    )
    monkeypatch.setattr(ManifestRegistry, "load", lambda: manifest)
    monkeypatch.setattr(
        "lintro.config.config_loader.get_config",
        lambda **kwargs: LintroConfig(),
    )
    monkeypatch.setattr(doctor_report, "_ai_checks", lambda **kwargs: [])
    return commands


@pytest.mark.parametrize(
    "surface",
    [
        "snapshots",
        "list-tools",
        "doctor-named",
        "doctor-all",
        "doctor-enabled",
        "mcp-doctor",
    ],
)
def test_introspection_does_not_spawn_fallback_but_probes_installed_tools(
    probe_commands: list[list[str]],
    tmp_path: Path,
    surface: str,
) -> None:
    """Every affected surface reports missing tools while probing installed peers."""
    if surface == "snapshots":
        rows = snapshots.probe_all_tools(tool_names=_TOOLS, for_introspection=True)
        assert_that(rows["stylelint"].available).is_false()
        assert_that(rows["stylelint"].probe_error).contains("registry fallback only")
        assert_that(rows["stylelint"].version).is_none()
        for name in _TOOLS[1:]:
            assert_that(rows[name].available).is_true()
            assert_that(rows[name].version).is_equal_to("99.0.0")
    elif surface == "list-tools":
        result = CliRunner().invoke(list_tools_command, ["--json"])
        assert_that(result.exit_code).is_equal_to(0)
        payload = json.loads(result.output)
        assert_that(payload["stylelint"]["status"]).is_equal_to("unavailable")
        assert_that(payload["stylelint"]["probe_error"]).contains(
            "registry fallback only",
        )
        for name in _TOOLS[1:]:
            assert_that(payload[name]["status"]).is_equal_to("ok")
            assert_that(payload[name]["version"]).is_equal_to("99.0.0")
    elif surface == "mcp-doctor":
        spec = next(
            spec
            for spec in build_introspection_toolkit(workspace=tmp_path)
            if spec.name == "lintro_doctor"
        )
        report = spec.handler({})
        checks = {check["check"]: check for check in report["checks"]}
        assert_that(checks["tools.missing"]["detail"]).is_equal_to(
            "1 enabled tool(s) not installed: stylelint",
        )
        assert_that(checks["tools.versions"]["status"]).is_equal_to("ok")
    else:
        tool_checks = doctor_report.collect_tool_checks(
            registry=ManifestRegistry.load(),
            context=RuntimeContext.detect(),
            config=LintroConfig(),
            tool_names=_TOOLS if surface == "doctor-named" else None,
            check_all=surface == "doctor-all",
        )
        by_name = {check.tool.name: check for check in tool_checks}
        assert_that(by_name["stylelint"].status).is_equal_to(ToolStatus.MISSING)
        for name in _TOOLS[1:]:
            assert_that(by_name[name].status).is_equal_to(ToolStatus.OK)
            assert_that(by_name[name].installed_version).is_equal_to("99.0.0")

    assert_that(
        [cmd for cmd in probe_commands if Path(cmd[0]).name in {"bunx", "npx"}],
    ).is_empty()
    assert_that(probe_commands).contains(
        [str(tmp_path / "prettier"), "--version"],
        ["shellcheck", "--version"],
        [sys.executable, "-m", "ruff", "--version"],
    )


@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
def test_introspection_diagnostics_do_not_replace_execution_fallback_evidence(
    probe_commands: list[list[str]],
    hydrate: bool,
    tmp_path: Path,
) -> None:
    """Listing remains offline before and after an execution probe uses the runner."""
    for _ in range(2):
        before = len(probe_commands)
        rows = snapshots.probe_all_tools(
            tool_names=["stylelint"],
            cache_root=tmp_path,
            for_introspection=True,
        )
        assert_that(rows["stylelint"].available).is_false()
        assert_that(rows["stylelint"].version_check_passed).is_false()
        assert_that(probe_commands[before:]).is_empty()
        cache = json.loads((tmp_path / snapshots.SNAPSHOT_CACHE_RELPATH).read_text())
        assert_that(cache["snapshots"]).does_not_contain_key("stylelint")
        if hydrate:
            snapshots.clear_snapshot_cache(include_disk=False)
        executable = snapshots.get_tool_snapshot("stylelint", cache_root=tmp_path)
        assert_that(executable.available).is_true()
        assert_that(executable.version_check_passed).is_true()
        assert_that(executable.version).is_equal_to("99.0.0")
        assert_that(executable.command[0]).is_in("bunx", "npx")
        assert_that(executable.command).is_equal_to(tuple(probe_commands[-1][:-1]))
        if hydrate:
            snapshots.clear_snapshot_cache(include_disk=False)
