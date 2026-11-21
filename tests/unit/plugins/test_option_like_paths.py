"""Registry-wide guard: a file named like an option never reaches a tool bare (#3584).

Every registered plugin runs its real ``check`` (and ``fix`` where it has one)
against a project holding files whose names start with ``-``. Only the stdlib
process boundary is stubbed, so discovery, path preparation and command
building all run. No recorded argv token may equal one of the planted names:
a tool would read such a token as an option, so a pull request could switch
the tool's checks off.

The comparison is by exact token against the names this test plants; it does
not reuse lintro's own path handling.
"""

from __future__ import annotations

import io
import subprocess  # nosec B404 - process results only; no real subprocesses
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.plugins.registry import ToolRegistry

# Names a tool would parse as options or as stdin; ``{ext}`` takes the
# extension of each plugin's first file pattern.
_OPTION_STEMS = ("--config=x", "-e", "-", "--force-exclude=", "--ignore-patterns=x")
# A directory named like an option covers plugins whose patterns are fixed
# names (``Cargo.lock``, ``Dockerfile``): the relative path still starts with
# ``-``.
_OPTION_DIR = "--dir=x"
# Names that are options on their own, whatever the extension.
_BARE_NAMES = ("-", "--", "-n")

_CARGO_PROJECT = {
    "Cargo.toml": '[package]\nname = "guard"\nversion = "0.1.0"\n',
    "src/main.rs": "fn main() {}\n",
    "Cargo.lock": "version = 3\n",
}
_BASE_LAYOUTS: dict[str, dict[str, str]] = {
    "actionlint": {".github/workflows/ci.yml": "name: CI\non: push\njobs: {}\n"},
    "cargo_audit": _CARGO_PROJECT,
    "cargo_deny": _CARGO_PROJECT,
    "clippy": _CARGO_PROJECT,
    "rustfmt": _CARGO_PROJECT,
    "golangci_lint": {
        "go.mod": "module example.com/guard\n\ngo 1.22\n",
        "main.go": "package main\nfunc main() {}\n",
    },
    "import-linter": {
        "pyproject.toml": '[tool.importlinter]\nroot_package = "guard"\n',
        "guard/__init__.py": "",
    },
    "osv_scanner": {"requirements.txt": "requests==2.32.0\n"},
    "pip_audit": {"requirements.txt": "requests==2.32.0\n"},
    "spectral": {".spectral.yaml": "extends: spectral:oas\n"},
    # Two sub-projects make the TypeScript checkers run per project; the
    # planted root files belong to neither and are passed on their own.
    "tsc": {
        "a/tsconfig.json": "{}\n",
        "a/index.ts": "export {};\n",
        "b/tsconfig.json": "{}\n",
        "b/index.ts": "export {};\n",
    },
}
# Plugins that never start a process for the files they are given.
_NO_SUBPROCESS = {
    "idiom-review": "Disabled AI review; it starts no tool process.",
}


def _planted_names(patterns: list[str], base_layout: dict[str, str]) -> list[str]:
    """Return option-like file names matching a plugin's discovery patterns.

    Args:
        patterns: The plugin's declared file patterns.
        base_layout: The plugin's minimal project, nested under an
            option-like directory when the plugin declares no patterns.

    Returns:
        Relative paths, each of which starts with ``-``.
    """
    names: list[str] = []
    for pattern in patterns:
        bare = pattern.removeprefix("**/")
        if bare == "*":
            names.extend(_BARE_NAMES)
            names.extend(f"{stem}.txt" for stem in _OPTION_STEMS if stem != "-")
        elif bare.startswith("*.") and bare.count("*") == 1:
            names.extend(f"{stem}{bare[1:]}" for stem in _OPTION_STEMS)
        if "/" not in bare and not any(char in bare for char in "?[]"):
            names.append(f"{_OPTION_DIR}/{bare.replace('*', 'guard')}")
    if not patterns:
        names.extend(f"{_OPTION_DIR}/{relative}" for relative in base_layout)
    return names


def _write(root: Path, relative: str, content: str) -> None:
    """Write one project file, creating its parents.

    Args:
        root: Project root.
        relative: Path below the root.
        content: File text.
    """
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _stub_processes(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace the stdlib process boundary with a recorder.

    Args:
        monkeypatch: Fixture restoring the patches.

    Returns:
        The list that receives every argv a plugin tries to run.
    """
    commands: list[list[str]] = []

    def completed(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str | bytes]:
        """Record argv and report a clean, empty run.

        Args:
            args: Command assembled by the plugin.
            **kwargs: Subprocess settings, including text mode.

        Returns:
            A completed process with exit code zero and no output.
        """
        commands.append([str(token) for token in args])
        text = kwargs.get("text", kwargs.get("universal_newlines", False))
        empty: str | bytes = "" if text else b""
        return subprocess.CompletedProcess[str | bytes](args, 0, empty, empty)

    def popen(args: list[str], **kwargs: object) -> MagicMock:
        """Record argv and hand back a finished process with no output.

        Args:
            args: Command assembled by the plugin.
            **kwargs: Subprocess settings, including text mode.

        Returns:
            A process double that has already exited with code zero.
        """
        commands.append([str(token) for token in args])
        text = kwargs.get("text", kwargs.get("universal_newlines", False))
        process = MagicMock()
        process.stdout = io.StringIO("") if text else io.BytesIO(b"")
        process.stderr = io.StringIO("") if text else io.BytesIO(b"")
        process.returncode = 0
        process.wait.return_value = 0
        process.poll.return_value = 0
        process.communicate.return_value = ("", "") if text else (b"", b"")
        process.__enter__.return_value = process
        return process

    monkeypatch.setattr("subprocess.run", completed)
    monkeypatch.setattr("subprocess.Popen", MagicMock(side_effect=popen))
    monkeypatch.setattr("shutil.which", MagicMock(return_value="/mock/bin/tool"))
    for gate in (
        "lintro.plugins.execution_preparation.verify_tool_version",
        "lintro.plugins.base.verify_tool_version",
        "lintro.tools.osv_scanner.definition.verify_tool_version",
    ):
        monkeypatch.setattr(gate, MagicMock(return_value=None))
    return commands


# Every plugin the guard runs, and whether it offers ``fix``. Explicit, like
# ``tests/unit/tools/test_failed_run_contract.py``: parameters built from the
# registry at collection time come out empty when another module has cleared
# it, and the guard would then pass while covering nothing.
_PLUGINS: dict[str, bool] = {
    "actionlint": False,
    "astro-check": False,
    "bandit": False,
    "black": True,
    "buf": True,
    "cargo_audit": False,
    "cargo_deny": False,
    "checkov": False,
    "clippy": True,
    "commitlint": False,
    "cppcheck": False,
    "dotenv_linter": True,
    "gitleaks": False,
    "golangci_lint": True,
    "hadolint": False,
    "html_validate": False,
    "import-linter": False,
    "markdownlint": False,
    "mypy": False,
    "osv_scanner": False,
    "oxfmt": True,
    "oxlint": True,
    "pip_audit": False,
    "prettier": True,
    "pydoclint": False,
    "pylint": False,
    "pytest": False,
    "ruff": True,
    "rustfmt": True,
    "semgrep": False,
    "shellcheck": False,
    "shfmt": True,
    "spectral": False,
    "sqlfluff": True,
    "stylelint": True,
    "svelte-check": False,
    "taplo": True,
    "trufflehog": False,
    "tsc": False,
    "typos": True,
    "vale": False,
    "vue-tsc": False,
    "yamllint": False,
}
_SCOPES = ("tree", "explicit")
# ``check`` for every plugin, plus ``fix`` where offered, each run once on the
# project root (``tree``) and once on the planted paths named one by one, as
# a user would pass them (``explicit``).
_RUNS = [
    (name, action, scope)
    for name, fixes in sorted(_PLUGINS.items())
    for action in (("check", "fix") if fixes else ("check",))
    for scope in _SCOPES
]


def test_every_plugin_is_covered_or_excluded_with_a_reason() -> None:
    """A new registration needs a guard decision; ``fix`` support must match."""
    registered = ToolRegistry.get_all()
    assert_that(set(_PLUGINS) & set(_NO_SUBPROCESS)).is_empty()
    assert_that(set(_PLUGINS) | set(_NO_SUBPROCESS)).is_equal_to(set(registered))
    assert_that(all(reason.strip() for reason in _NO_SUBPROCESS.values())).is_true()
    offers_fix = {
        name: registered[name].definition.can_fix for name in sorted(_PLUGINS)
    }
    assert_that(offers_fix).is_equal_to(_PLUGINS)


@pytest.mark.parametrize(
    ("tool_name", "action", "scope"),
    _RUNS,
    ids=["-".join(run) for run in _RUNS],
)
def test_no_option_like_path_reaches_a_tool(
    tool_name: str,
    action: str,
    scope: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No argv token handed to a tool is a planted name starting with ``-``.

    Args:
        tool_name: Registered plugin under test.
        action: ``check`` or ``fix``.
        scope: ``tree`` passes ``.``; ``explicit`` passes each planted path.
        monkeypatch: Fixture for the process boundary and working directory.
        tmp_path: Project directory holding the planted files.
    """
    plugin = type(ToolRegistry.get(tool_name))()
    base_layout = _BASE_LAYOUTS.get(tool_name, {})
    for relative, content in base_layout.items():
        _write(tmp_path, relative, content)
    planted = _planted_names(list(plugin.definition.file_patterns), base_layout)
    for relative in planted:
        _write(tmp_path, relative, "x = 1\n")
    # Each planted path, its first component and an option-like basename are
    # all tokens a tool would misread if handed over bare.
    forbidden = set(planted)
    forbidden |= {relative.split("/", 1)[0] for relative in planted}
    forbidden |= {
        base
        for relative in planted
        if (base := relative.rsplit("/", 1)[-1]).startswith("-")
    }
    assert_that(planted).is_not_empty()
    assert_that(all(name.startswith("-") for name in forbidden)).is_true()

    commands = _stub_processes(monkeypatch)
    monkeypatch.chdir(tmp_path)
    run = plugin.fix if action == "fix" else plugin.check
    run(["."] if scope == "tree" else planted, {})

    leaked = sorted(
        {token for argv in commands for token in argv if token in forbidden},
    )
    assert_that(leaked).described_as(
        f"{tool_name} {action}: option-like paths handed over bare in {commands}",
    ).is_empty()
