"""Hand-edited seed mapping: external package name to ToolName or None.

This module is the only hand-maintained piece of the tool-version system.
Everything else (`lintro/_generated_versions.py`, version fields in
`lintro/tools/manifest.json`) is produced by
`scripts/ci/generate-tool-versions.py` from these seeds combined with
`package.json` and `pyproject.toml`.

A `None` value marks a companion package: a dependency that lintro pins
because it ships alongside a tool, but which is not itself exposed as a
user-facing tool. Companions still flow through the generator so their
versions stay in lockstep with the rest of the install set.

Adding a new npm or pypi tool:
    1. Add the `ToolName` enum member in `lintro/enums/tool_name.py`.
    2. Add the package name to the appropriate mapping below.
    3. Pin the package in `package.json` (npm) or `pyproject.toml` (pypi).
    4. Run `python3 scripts/ci/generate-tool-versions.py`.

Tools installed from neither npm nor pypi (standalone binaries, cargo,
rustup) live in `TOOL_VERSIONS` in `lintro/_tool_versions.py` and are
updated by Renovate via custom regex managers in `renovate.json`.
"""

from __future__ import annotations

from lintro.enums.tool_name import ToolName

NPM_PACKAGE_OWNERS: dict[str, ToolName | None] = {
    "astro": ToolName.ASTRO_CHECK,
    "html-validate": ToolName.HTML_VALIDATE,
    "svelte-check": ToolName.SVELTE_CHECK,
    "typescript": ToolName.TSC,
    "vue-tsc": ToolName.VUE_TSC,
    "prettier": ToolName.PRETTIER,
    "markdownlint-cli2": ToolName.MARKDOWNLINT,
    "oxlint": ToolName.OXLINT,
    "oxfmt": ToolName.OXFMT,
    "@astrojs/check": None,
}

PYPI_PACKAGE_OWNERS: dict[str, ToolName | None] = {
    "bandit": ToolName.BANDIT,
    "black": ToolName.BLACK,
    "mypy": ToolName.MYPY,
    "pydoclint": ToolName.PYDOCLINT,
    "pytest": ToolName.PYTEST,
    "ruff": ToolName.RUFF,
    "semgrep": ToolName.SEMGREP,
    "sqlfluff": ToolName.SQLFLUFF,
    "yamllint": ToolName.YAMLLINT,
}
