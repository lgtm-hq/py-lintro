"""Real plugins record only the discovered paths they state they handed (#3578).

The subprocess layer is replaced by a stub that keeps every argv it receives
and answers "exit 0, no output". Nothing here classifies argv tokens the way
production code might: a discovered path counts as received only when some
token is that exact path, or that exact path written relative to the
spawn's working directory.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools import tool_manager
from lintro.tools.spectral.definition import SpectralPlugin
from lintro.utils.file_cache import ToolCache
from lintro.utils.incremental_record import finish_incremental_run

#: Sample files with names no subcommand or option value could produce.
SAMPLES = {
    "zq_alpha.py": '"""Module."""\n\nX = 1\n',
    "zq_beta.sh": "#!/bin/sh\necho hi\n",
    "zq_gamma.toml": "a = 1\n",
    "zq_delta.yaml": "a: 1\n",
    "zq_eps.json": '{"a": 1}\n',
    "zq_zeta.md": "# Title\n",
    "zq_eta.js": "let x = 1;\n",
    "zq_theta.ts": "const x: number = 1;\n",
    "zq_iota.css": "a { color: red; }\n",
    "zq_kappa.html": "<!doctype html><html></html>\n",
    "zq_lambda.sql": "select 1;\n",
    "zq_mu.tf": 'resource "x" "y" {}\n',
    "zq_nu.c": "int main(void) { return 0; }\n",
    "zq_xi.txt": "plain text\n",
    "Dockerfile": "FROM scratch\n",
    ".env": "A=1\n",
    ".github/workflows/zq_omicron.yml": "on: push\njobs: {}\n",
}


class _Stub:
    """Stand-in for the subprocess layer that remembers what it was given."""

    def __init__(self) -> None:
        """Start with no recorded commands."""
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, cmd: list[str], *args: Any, **kwargs: Any) -> SubprocessResult:
        """Record one spawn and answer with a clean, empty run.

        Args:
            cmd: The argv lintro asked to spawn.
            *args: Positional arguments of the real function (timeout, cwd).
            **kwargs: Keyword arguments of the real function.

        Returns:
            Exit 0 with no output; an empty JSON array for ``ruff check`` and
            spectral, whose reports are JSON.
        """
        cwd = kwargs.get("cwd", args[1] if len(args) > 1 else None)
        self.calls.append((list(cmd), cwd or os.getcwd()))
        # Decided from the whole argv, not a position in it: how a tool is
        # launched (a PATH binary, ``python -m``, ``bunx``, ``npx --yes ...``)
        # depends on the machine.
        json_report = ("check" in cmd and any("ruff" in t for t in cmd)) or any(
            "spectral" in token for token in cmd
        )
        out = "[]" if json_report else ""
        return SubprocessResult(returncode=0, stdout=out, stderr="", output=out)

    def received(self, paths: set[str]) -> set[str]:
        """Return the discovered paths some argv carried as an exact token.

        Args:
            paths: Discovered paths, as discovery keyed them.

        Returns:
            The paths written verbatim, or verbatim relative to that spawn's
            working directory, in at least one recorded argv.
        """
        found: set[str] = set()
        for cmd, cwd in self.calls:
            for token in cmd:
                for path in paths:
                    if token == path or token == os.path.relpath(path, cwd):
                        found.add(path)
        return found


@pytest.fixture
def stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_Stub]:
    """Stub every spawn and the version gate, and isolate the cache.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Patches the cache location and the working directory.

    Yields:
        _Stub: The stub recording every argv.
    """
    monkeypatch.setattr("lintro.utils.file_cache.CACHE_DIR", tmp_path / "cache")
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    recorder = _Stub()
    refused = OSError("spawn stubbed out")
    with (
        patch("lintro.plugins.base.run_subprocess", side_effect=recorder),
        patch("lintro.plugins.base.run_subprocess_streaming", side_effect=recorder),
        patch("lintro.tools.bandit.definition.subprocess.run", side_effect=refused),
        patch(
            "lintro.tools.html_validate.one_process.subprocess.Popen",
            side_effect=refused,
        ),
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
    ):
        yield recorder


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    """Create files below ``root``.

    Args:
        root: Project directory.
        files: Relative path to text or bytes content.
    """
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def _run(name: str, **options: object) -> BaseToolPlugin:
    """Run one registered plugin's check under ``--incremental``.

    Args:
        name: Registered tool name.
        **options: Tool options to set before the run.

    Returns:
        The execution copy that ran, holding its pending and stated paths.
    """
    tool = tool_manager.get_tool(name).copy_for_execution()
    tool.set_options(incremental=True, **options)
    # A stubbed spawn may make a plugin fail in any way; only what it
    # stated before failing matters here.
    with suppress(Exception):
        tool.check(["."], {})
    return tool


def _pending(tool: BaseToolPlugin) -> set[str]:
    """Return the discovered paths a run handed its plugin.

    Args:
        tool: The execution copy that ran.

    Returns:
        Discovery keys of every pending file.
    """
    return {
        path
        for check in tool.incremental_pending
        for path in check.snapshot.fingerprints
    }


def _record_clean(tool: BaseToolPlugin) -> list[str]:
    """Finish the run as a complete, clean check and read back the cache.

    The verdict is forced clean so the plugin's statement alone decides.

    Args:
        tool: The execution copy that ran.

    Returns:
        File names recorded in the tool's cache, sorted.
    """
    clean = ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=True, inspected_files=True)),
        name=tool.definition.name,
    )
    finish_incremental_run(tool, clean, record=True)
    return sorted(
        os.path.relpath(path)
        for path in ToolCache.load(tool.definition.name).fingerprints
    )


def test_every_plugin_states_only_paths_its_tool_received(stub: _Stub) -> None:
    """No registered plugin states a path that no spawned argv carried.

    Args:
        stub: Records every argv.
    """
    _write(Path.cwd(), dict(SAMPLES))
    stating: list[str] = []
    for name in sorted(tool_manager.get_tool_names()):
        stub.calls.clear()
        tool = _run(name)
        stated = set(tool.incremental_checked)
        if stated:
            stating.append(name)
        unreceived = stated - stub.received(_pending(tool))
        assert_that(sorted(unreceived)).described_as(name).is_empty()

    assert_that(stating).contains(
        "checkov",
        "cppcheck",
        "hadolint",
        "prettier",
        "pylint",
        "ruff",
        "shellcheck",
        "taplo",
        "typos",
    )


def test_shellcheck_with_external_sources_states_its_file(stub: _Stub) -> None:
    """An option that once looked like a config flag no longer drops files.

    Args:
        stub: Records every argv.
    """
    _write(Path.cwd(), {"zq_beta.sh": SAMPLES["zq_beta.sh"]})

    recorded = _record_clean(_run("shellcheck", external_sources=True))

    assert_that(recorded).is_equal_to(["zq_beta.sh"])


def test_gitleaks_never_records_a_file_named_like_its_subcommand(stub: _Stub) -> None:
    """A file called ``detect`` is not taken as scanned by ``gitleaks detect``.

    Args:
        stub: Records every argv.
    """
    _write(Path.cwd(), {"detect": "token = ghp_example\n", "zq_xi.txt": "x\n"})

    recorded = _record_clean(_run("gitleaks", no_git=False))

    assert_that(recorded).is_empty()


def test_typos_records_every_text_file_and_never_its_format_value(
    stub: _Stub,
) -> None:
    """A binary file named ``json`` is skipped; both text files are recorded.

    Args:
        stub: Records every argv.
    """
    _write(
        Path.cwd(),
        {"a_clean.txt": "fine\n", "b_clean.txt": "fine\n", "json": b"\x00\x01"},
    )

    recorded = _record_clean(_run("typos"))

    assert_that(recorded).is_equal_to(["a_clean.txt", "b_clean.txt"])


def test_pylint_records_the_included_link_not_its_excluded_target(
    stub: _Stub,
) -> None:
    """A symlink inside ``include`` does not record the file it points to.

    Args:
        stub: Records every argv.
    """
    root = Path.cwd()
    _write(root, {"b_excluded/target.py": '"""T."""\n'})
    (root / "a_included").mkdir()
    (root / "a_included" / "link.py").symlink_to(root / "b_excluded" / "target.py")

    recorded = _record_clean(_run("pylint", include="a_included"))

    assert_that(recorded).is_equal_to([os.path.join("a_included", "link.py")])


def test_spectral_never_records_the_target_of_a_checked_link(stub: _Stub) -> None:
    """A file outside every ruleset stays unrecorded, even behind a link.

    Args:
        stub: Records every argv.
    """
    root = Path.cwd()
    _write(
        root,
        {
            "a_with_rs/.spectral.yaml": "extends: []\n",
            "z_no_rs/target.yaml": "openapi: 3.0.0\n",
        },
    )
    (root / "a_with_rs" / "link.yaml").symlink_to(root / "z_no_rs" / "target.yaml")

    # Pin the launcher so the run does not depend on which Node.js runner
    # (bunx, npx) the machine has.
    with patch.object(
        SpectralPlugin,
        "_get_spectral_command",
        return_value=["spectral"],
    ):
        recorded = _record_clean(_run("spectral"))

    assert_that(recorded).contains(os.path.join("a_with_rs", "link.yaml"))
    assert_that(recorded).does_not_contain(os.path.join("z_no_rs", "target.yaml"))


def test_bandit_records_the_files_its_report_says_it_scanned(stub: _Stub) -> None:
    """Bandit states the files its metrics name, not every file it was given.

    Args:
        stub: Records every argv.
    """
    root = Path.cwd()
    _write(root, {"scanned.py": "X = 1\n", "excluded.py": "Y = 2\n"})
    report = {
        "errors": [],
        "results": [],
        "metrics": {str(root / "scanned.py"): {"loc": 1}, "_totals": {"loc": 1}},
    }
    answer = SimpleNamespace(returncode=0, stdout=json.dumps(report), stderr="")

    with patch("lintro.tools.bandit.definition.subprocess.run", return_value=answer):
        recorded = _record_clean(_run("bandit"))

    assert_that(recorded).is_equal_to(["scanned.py"])
