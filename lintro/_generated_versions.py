"""Auto-generated tool versions. Do not edit by hand.

Run ``python3 scripts/ci/generate-tool-versions.py`` to regenerate.

Sources:
    - package.json (npm devDependencies)
    - pyproject.toml (pypi dependency tables)
    - lintro/_tool_packages.py (seed mapping)
"""

NPM_VERSIONS: dict[str, str] = {
    "@astrojs/check": "0.9.8",
    "astro": "6.1.6",
    "markdownlint-cli2": "0.22.0",
    "oxfmt": "0.43.0",
    "oxlint": "1.58.0",
    "prettier": "3.8.1",
    "svelte-check": "4.4.6",
    "typescript": "5.9.3",
    "vue-tsc": "3.2.6",
}

PYPI_VERSIONS: dict[str, str] = {
    "bandit": "1.9.4",
    "black": "26.3.1",
    "mypy": "1.19.1",
    "pydoclint": "0.8.3",
    "pytest": "9.0.3",
    "ruff": "0.15.9",
    "semgrep": "1.151.0",
    "sqlfluff": "4.0.0",
    "yamllint": "1.37.1",
}
