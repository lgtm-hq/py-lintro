"""Registry-wide guard: a file named like an option never reaches a tool bare (#3584).

Every registered plugin runs its real ``check`` (and ``fix`` where it has one)
against a project holding files whose names start with ``-`` (an option),
``@`` (an argument file for tsc, mypy, pytest and trufflehog), ``#`` or ``!``
(skipped by markdownlint-cli2). Only
the stdlib process boundary is stubbed, so discovery, path preparation and
command building all run. Two things must hold:

- no recorded argv token equals a planted name, because a tool would read it
  as an option or argument file; and
- every planted file still reaches the tool, under a token that names it
  without a special first character, so a plugin cannot pass by dropping its
  file arguments.

The comparisons use the names this test plants and the working directory of
each recorded command; they do not reuse lintro's own path handling.
"""

from __future__ import annotations

import io
import os
import subprocess  # nosec B404 - process results only; no real subprocesses
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.plugins.registry import ToolRegistry

# First characters a wrapped tool reads specially, as this test plants them.
_SPECIAL_FIRST = ("-", "@", "#", "!")
# Names a tool would parse as options, stdin or argument files; each takes the
# extension of every ``*.ext`` pattern a plugin declares.
_OPTION_STEMS = (
    "--config=x",
    "-e",
    "-",
    "--force-exclude=",
    "--ignore-patterns=x",
    "@args",
    "#x",
    "!x",
)
# Directories named like options cover plugins whose patterns are fixed
# names (``Cargo.lock``, ``Dockerfile``): the relative path still starts with
# a special character.
_OPTION_DIRS = ("--dir=x", "@dir")
# Names that are options or argument files on their own, whatever the
# extension.
_BARE_NAMES = ("-", "--", "-n", "@args", "#x", "!x")

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
    # A config at the root makes yamllint run from there on relative paths.
    "yamllint": {".yamllint": "extends: default\n"},
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
        Relative paths, each of which starts with a special character.
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
            names.extend(
                f"{folder}/{bare.replace('*', 'guard')}" for folder in _OPTION_DIRS
            )
    if not patterns:
        names.extend(
            f"{folder}/{relative}"
            for folder in _OPTION_DIRS
            for relative in base_layout
        )
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


def _stub_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[list[str], str]]:
    """Replace the stdlib process boundary with a recorder.

    Args:
        monkeypatch: Fixture restoring the patches.

    Returns:
        The list that receives every argv a plugin tries to run, with the
        working directory it would run in.
    """
    commands: list[tuple[list[str], str]] = []

    def record(args: list[str], kwargs: dict[str, object]) -> None:
        """Store argv and the directory relative tokens resolve against.

        Args:
            args: Command assembled by the plugin.
            kwargs: Subprocess settings, including ``cwd``.
        """
        cwd = kwargs.get("cwd")
        if "--report-path" in args:
            # gitleaks reads its findings from this file; an empty report
            # lets it go on to the next source.
            report = Path(str(args[list(args).index("--report-path") + 1]))
            report.write_text("[]", encoding="utf-8")
        commands.append(
            ([str(token) for token in args], str(cwd) if cwd else os.getcwd()),
        )

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
        record(args, kwargs)
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
        record(args, kwargs)
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
_BOTH = frozenset({"tree", "explicit"})
_TREE = frozenset({"tree"})
# Plugins that hand a directory over instead of naming each file, in the
# scopes listed. Every other plugin must name each planted file.
_NAMES_A_DIRECTORY: dict[str, tuple[frozenset[str], str]] = {
    "gitleaks": (_TREE, "Scans the project as one --source directory."),
    "mypy": (_TREE, "Passes the target directory and lets mypy find modules."),
    "osv_scanner": (_BOTH, "Scans the project root recursively."),
    "pip_audit": (_BOTH, "Audits project directories and requirements files."),
    "trufflehog": (_TREE, "Scans the project directory."),
}
# Plugins that take no file arguments at all in the scopes listed; for them
# only the absence of bare names is checked.
_NAMES_NO_FILES: dict[str, tuple[frozenset[str], str]] = {
    "astro-check": (_BOTH, "Checks the Astro project; no file arguments."),
    "cargo_audit": (_BOTH, "Audits Cargo.lock files by --file=<absolute>."),
    "cargo_deny": (_BOTH, "Runs cargo on one crate; skips a multi-crate tree."),
    "clippy": (_BOTH, "Runs cargo on one crate; skips a multi-crate tree."),
    "commitlint": (_BOTH, "Lints the last commit message (--last)."),
    "golangci_lint": (_BOTH, "Runs on the Go module (./...)."),
    "import-linter": (_BOTH, "Reads its contracts from the config file."),
    "pytest": (_TREE, "Runs the default tests directory."),
    "rustfmt": (_BOTH, "Runs cargo fmt on the crate."),
    "svelte-check": (_BOTH, "Checks the Svelte project; no file arguments."),
}
# Plugins that only consider files under a fixed directory.
_PLANT_UNDER = {"actionlint": ".github/workflows/"}
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
    for table in (_NAMES_A_DIRECTORY, _NAMES_NO_FILES):
        assert_that(set(table) - set(_PLUGINS)).is_empty()
        assert_that(all(reason.strip() for _, reason in table.values())).is_true()
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
    prefix = _PLANT_UNDER.get(tool_name, "")
    planted = [
        prefix + name
        for name in _planted_names(list(plugin.definition.file_patterns), base_layout)
    ]
    for relative in planted:
        _write(tmp_path, relative, "x = 1\n")
    # Each planted path and every special component of it are tokens a tool
    # would misread if handed over bare.
    forbidden = set(planted) - {""}
    forbidden |= {
        part
        for relative in planted
        for part in relative.split("/")
        if part.startswith(_SPECIAL_FIRST)
    }
    assert_that(planted).is_not_empty()
    assert_that(
        all(name.removeprefix(prefix).startswith(_SPECIAL_FIRST) for name in forbidden),
    ).is_true()

    commands = _stub_processes(monkeypatch)
    monkeypatch.chdir(tmp_path)
    run = plugin.fix if action == "fix" else plugin.check
    run(["."] if scope == "tree" else planted, {})

    leaked = sorted(
        {token for argv, _ in commands for token in argv if token in forbidden},
    )
    assert_that(leaked).described_as(
        f"{tool_name} {action}: option-like paths handed over bare in {commands}",
    ).is_empty()
    if scope in _NAMES_NO_FILES.get(tool_name, (frozenset(), ""))[0]:
        return
    runs = [argv for argv, _ in commands if not _VERSION_PROBES.intersection(argv)]
    assert_that(runs).described_as(f"{tool_name} {action}: no tool ran").is_not_empty()
    _assert_files_reach_the_tool(tool_name, scope, planted, commands, tmp_path)


_VERSION_PROBES = frozenset({"--version", "-V", "version"})


def _named_path(token: str, cwd: str) -> str | None:
    """Return the real path a token names, if it can name a planted file.

    Args:
        token: One argv token, possibly ``--option=<path>``.
        cwd: Directory the command runs in.

    Returns:
        The resolved path, or None when the token would be read specially.
    """
    if token.startswith("-") and "=" in token:
        token = token.split("=", 1)[1]
    if not token or token.startswith(_SPECIAL_FIRST):
        return None
    return os.path.realpath(os.path.join(cwd, token))


def _assert_files_reach_the_tool(
    tool_name: str,
    scope: str,
    planted: list[str],
    commands: list[tuple[list[str], str]],
    root: Path,
) -> None:
    """Require each planted file to reach the tool under a safe token.

    Args:
        tool_name: Plugin under test.
        scope: ``tree`` or ``explicit``.
        planted: Planted relative paths.
        commands: Recorded argv and working directory pairs.
        root: Project root.
    """
    named = {
        path
        for argv, cwd in commands
        for token in argv
        if (path := _named_path(token, cwd)) is not None
    }
    real_root = os.path.realpath(root)
    for relative in planted:
        target = os.path.join(real_root, relative)
        if scope in _NAMES_A_DIRECTORY.get(tool_name, (frozenset(), ""))[0]:
            ancestors = {real_root, os.path.dirname(target), target}
            reached = bool(named & ancestors)
        else:
            reached = target in named
        assert_that(reached).described_as(
            f"{tool_name} {scope}: {relative!r} never reached the tool: {commands}",
        ).is_true()
