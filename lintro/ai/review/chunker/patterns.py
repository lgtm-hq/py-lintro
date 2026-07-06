"""Compiled regex fragments for workflow script-reference matching.

Regex vocabulary used by ``workflow_scripts.py`` to recognise GitHub Actions
``run:``/``uses:`` steps, interpreter and ``uv run`` command prefixes, and
non-invoking shell builtins. Kept beside :mod:`vocabulary` so the pattern
layer can be read in one place, separate from the matcher logic.

Fragment strings (``_..._PREFIX``, ``_..._STEP``) are reused to build
per-path matchers at call time; ``re.compile``d constants are ready to use.
"""

from __future__ import annotations

import re

# Leading ``${{ github.workspace }}/`` prefix on a shell path token.
_GITHUB_WORKSPACE_PREFIX = r"\$\{\{\s*github\.workspace\s*\}\}/"

# --- Runtime / uv run command prefixes --------------------------------------
_SHELL_FLAG = r"(?:\s+-[\w-]+(?:=\S+)?)*"
_WORKFLOW_SCRIPT_RUNTIMES = r"(?:bash|sh|python3?|node|bun)"
_RUN_INTERPRETER = rf"{_WORKFLOW_SCRIPT_RUNTIMES}{_SHELL_FLAG}\s+"
_UV_OPTION = (
    r"(?:"
    r"\s+--(?:with|directory|project|package)\s+\S+"
    r"|\s+--[\w-]+(?:=(?:[^\s\"']+|\"[^\"]*\"|'[^']*'))?"
    r")*"
)
_UV_RUN_WRAPPER = (
    rf"uv\s+run{_UV_OPTION}(?:\s+{_WORKFLOW_SCRIPT_RUNTIMES}){_SHELL_FLAG}\s+"
)
_RUN_COMMAND_PREFIX = rf"(?:{_RUN_INTERPRETER}|{_UV_RUN_WRAPPER}|uv\s+run\s+)"

# --- YAML step prefixes -----------------------------------------------------
_YAML_STEP_PREFIX = r"^[ \t]*(?:-\s+)?"
_RUN_STEP = rf"{_YAML_STEP_PREFIX}run:"
_USES_STEP = rf"{_YAML_STEP_PREFIX}uses:\s*"
_QUOTED_RUN_PATH = r'["\'](?:\./)?'

# --- Shell builtins / non-invocation detection ------------------------------
_FAIL_FAST_OR_TAIL = re.compile(
    r"(?i)^(?:exit(?:\s+\d+)?|false|return(?:\s+\d+)?|:)\b",
)
_NON_EXECUTION_COMMAND = re.compile(
    r"(?i)^(?:grep|cat|head|tail|less|more|sed|awk|sort|uniq|wc|find|ls|stat|"
    r"read|diff|cmp|strings|od|xxd|echo|printf|chmod|chown|chgrp|cp|mv|install|"
    r"ln|rsync|scp|touch|rm)\b",
)
_ASSIGNMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
