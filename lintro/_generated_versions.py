"""Auto-generated tool versions. Do not edit by hand.

Run ``python3 scripts/ci/generate-tool-versions.py`` to regenerate.

Sources:
    - package.json (npm devDependencies)
    - pyproject.toml (pypi dependency tables)
    - lintro/_tool_packages.py (seed mapping)
"""

NPM_VERSIONS: dict[str, str] = {
    "@astrojs/check": "0.9.9",
    "@commitlint/cli": "21.2.1",
    "@commitlint/config-conventional": "21.2.0",
    "astro": "7.1.3",
    "html-validate": "11.5.5",
    "markdownlint-cli2": "0.23.0",
    "oxfmt": "0.58.0",
    "oxlint": "1.73.0",
    "prettier": "3.9.4",
    "stylelint": "17.14.0",
    "svelte-check": "4.7.2",
    "typescript": "6.0.3",
    "vue-tsc": "3.3.7",
}

PYPI_VERSIONS: dict[str, str] = {
    "bandit": "1.9.4",
    "black": "26.3.1",
    "mypy": "1.19.1",
    "pip-audit": "2.10.1",
    "pydoclint": "0.8.3",
    "pytest": "9.0.3",
    "ruff": "0.15.9",
    "semgrep": "1.151.0",
    "sqlfluff": "4.0.0",
    "yamllint": "1.37.1",
}
