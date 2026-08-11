"""Auto-generated index of builtin tool definition modules.

Do not edit by hand. Run
``python3 scripts/ci/generate-builtin-tool-index.py`` to regenerate.

Names are module base names under ``lintro/tools/definitions/``. Discovery
imports them to populate the tool registry. Shipping the list as code (rather
than globbing ``lintro/tools/definitions/*.py``) keeps builtin discovery
working inside frozen Nuitka onefile binaries, which never materialize the
Python source directory (#2006).
"""

from __future__ import annotations

BUILTIN_TOOL_MODULES: tuple[str, ...] = (
    "actionlint",
    "astro_check",
    "bandit",
    "black",
    "cargo_audit",
    "cargo_deny",
    "clippy",
    "commitlint",
    "dotenv_linter",
    "gitleaks",
    "golangci_lint",
    "hadolint",
    "html_validate",
    "idiom_review",
    "markdownlint",
    "mypy",
    "osv_scanner",
    "oxfmt",
    "oxlint",
    "oxlint_doctor",
    "pip_audit",
    "prettier",
    "pydoclint",
    "pytest",
    "ruff",
    "rustfmt",
    "semgrep",
    "shellcheck",
    "shfmt",
    "sqlfluff",
    "stylelint",
    "svelte_check",
    "taplo",
    "trufflehog",
    "tsc",
    "vale",
    "vue_tsc",
    "yamllint",
)
