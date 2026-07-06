"""Shell/CI parsing vocabulary for workflow script-reference matching.

Static lexicon used by ``workflow_scripts.py`` to strip runtime wrappers,
resolve local action roots, and detect non-invoking commands. These are
parser lexicon / registry data (not pipeline ``models/`` types), colocated
here so CI/shell knowledge can be audited and unit-tested in one place
without reading the matcher implementation.

Symbols stay private (``_``-prefixed); ``workflow_scripts.py`` imports the
groups it needs. ``patterns.py`` holds the compiled regex fragments.
"""

from __future__ import annotations

# --- Runtime CLI ------------------------------------------------------------
# Flag/operand stripping for uv/node/bun/env when they precede an invoked
# script path. Long options in the ``*_LONG_OPERAND_OPTIONS`` sets consume a
# separate following operand; ``*_SHORT_OPERAND_OPTIONS`` do the same for
# clustered short flags; ``*_TERMINATING_*`` end execution before any script.
_UV_ARG_OPTIONS = frozenset({"with", "directory", "project", "package", "python"})
_UV_SHORT_OPERAND_OPTIONS = frozenset({"p", "w"})
_BUN_SUBCOMMANDS = frozenset({"run"})
_NODE_LONG_OPERAND_OPTIONS = frozenset({"import", "loader", "require"})
_BUN_LONG_OPERAND_OPTIONS = frozenset({"env-file", "preload", "require"})
_NODE_SHORT_OPERAND_OPTIONS = frozenset({"r"})
_RUNTIME_TERMINATING_LONG_OPTIONS = frozenset({"help", "version"})
_RUNTIME_TERMINATING_SHORT_OPTIONS = frozenset({"h", "v"})
_ENV_OPERAND_FLAGS = frozenset({"-u", "-U", "-C", "-S"})
_ENV_OPERAND_LONG_OPTIONS = frozenset({"chdir", "split-string"})

# --- Dispatch wrappers ------------------------------------------------------
# Wrappers that still execute the wrapped command. ``_SHELL_DISPATCH_WRAPPERS``
# (exec/command) and ``_SHELL_COMPOUND_LEADERS`` (then/do/...) are dropped
# outright; ``_COMMAND_DISPATCH_WRAPPERS`` (sudo/timeout) additionally consume
# their own option flags and mandatory positional operands before the command.
_SHELL_DISPATCH_WRAPPERS = frozenset({"exec", "command"})
_SHELL_COMPOUND_LEADERS = frozenset({"then", "do", "else", "elif"})
_COMMAND_DISPATCH_WRAPPERS = frozenset({"sudo", "timeout"})
_DISPATCH_OPERAND_FLAGS: dict[str, frozenset[str]] = {
    "sudo": frozenset(
        {
            "-u",
            "-g",
            "-C",
            "-p",
            "-r",
            "-t",
            "-U",
            "-h",
            "-R",
            "--user",
            "--group",
            "--prompt",
            "--chdir",
            "--role",
            "--type",
        },
    ),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
}
# Mandatory positional operands consumed before the wrapped command (for
# example ``timeout DURATION command``).
_DISPATCH_POSITIONAL_OPERANDS: dict[str, int] = {"sudo": 0, "timeout": 1}

# --- Action layout ----------------------------------------------------------
# Local action root resolution and workflow-linked file detection. Build
# artifact / source dirs bound the walk up to an action root; manifest names
# and non-executable suffixes decide whether a changed file is relevant to the
# workflow that invokes the action.
_NON_EXECUTABLE_WORKFLOW_SUFFIXES = frozenset(
    {".cfg", ".ini", ".json", ".md", ".rst", ".toml", ".txt", ".yaml", ".yml"},
)
_ACTION_BUILD_ARTIFACT_DIRS = frozenset(
    {"coverage", "dist", "lib", "node_modules", "out", "vendor"},
)
_ACTION_SOURCE_LAYOUT_DIRS = frozenset({"source", "src"})
# Manifests that define a local action's runtime dependencies or entrypoint, so
# changes to them are relevant to the workflow that invokes the action. Kept
# separate from ``classifier._DEPENDENCY_MANIFEST_NAMES``: that set classifies
# any changed file into a dependency-review domain across languages
# (cargo.toml, go.mod, pyproject.toml, npm-shrinkwrap.json, ...), whereas this
# set is scoped to JS local-action entrypoint/runtime manifests and lockfiles
# (including yarn.lock). The overlap is incidental, so they are not shared.
_ACTION_MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "yarn.lock",
    },
)
