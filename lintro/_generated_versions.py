"""Auto-generated tool versions. Do not edit by hand.

Run ``python3 scripts/ci/generate-tool-versions.py`` to regenerate.

Sources:
    - package.json (npm devDependencies)
    - pyproject.toml (pypi dependency tables)
    - requirements-semgrep.txt (isolated semgrep pin)
    - lintro/_tool_packages.py (seed mapping)
"""

NPM_VERSIONS: dict[str, str] = {
    "@astrojs/check": "0.9.10",
    "@commitlint/cli": "21.2.2",
    "@commitlint/config-conventional": "21.2.2",
    "astro": "7.2.2",
    "html-validate": "11.6.2",
    "markdownlint-cli2": "0.23.2",
    "oxfmt": "0.63.0",
    "oxlint": "1.78.0",
    "prettier": "3.9.4",
    "stylelint": "17.14.1",
    "svelte-check": "4.7.6",
    "typescript": "6.0.3",
    "vue-tsc": "3.3.10",
}

PYPI_VERSIONS: dict[str, str] = {
    "bandit": "1.9.4",
    "black": "26.3.1",
    "mypy": "1.19.1",
    "pip-audit": "2.10.1",
    "pydoclint": "0.8.3",
    "pytest": "9.0.3",
    "ruff": "0.15.9",
    "semgrep": "1.173.0",
    "sqlfluff": "4.0.0",
    "yamllint": "1.37.1",
}
