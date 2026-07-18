"""Documentation URL templates for linting tools.

Central registry of URL patterns used by tool plugins to generate
documentation links for rule codes. Templates use ``{code}`` as a
placeholder for the normalized rule identifier.

Tools with non-trivial code normalization (e.g., Ruff resolving a code
to a rule-name slug, Hadolint routing DL/SC prefixes) perform that
work in their ``doc_url()`` method before formatting the template.
"""

from __future__ import annotations

from enum import StrEnum


class DocUrlTemplate(StrEnum):
    """URL templates for tool rule documentation.

    Each value is a URL pattern. Templates containing ``{code}`` are
    formatted with the (possibly normalized) rule code at call time.
    Templates without ``{code}`` point to a single documentation page
    shared by all rules of that tool.
    """

    ACTIONLINT = "https://github.com/rhysd/actionlint/blob/main/docs/checks.md"
    ASTRO_CHECK = "https://docs.astro.build/en/guides/typescript/"
    BANDIT = "https://bandit.readthedocs.io/en/latest/plugins/index.html"
    CARGO_AUDIT = "https://rustsec.org/advisories/{code}"
    CARGO_DENY = "https://embarkstudios.github.io/cargo-deny/"
    CLIPPY = "https://rust-lang.github.io/rust-clippy/master/index.html#{code}"
    COMMITLINT = "https://commitlint.js.org/reference/rules.html"
    DJLINT = "https://www.djlint.com/docs/linter/"
    DOTENV_LINTER = "https://dotenv-linter.github.io/#/checks/{code}"
    HADOLINT = "https://github.com/hadolint/hadolint/wiki/{code}"
    MARKDOWNLINT = "https://github.com/DavidAnson/markdownlint/blob/main/doc/{code}.md"
    MYPY = "https://mypy.readthedocs.io/en/stable/error_code_list.html"
    OSV = "https://osv.dev/vulnerability/{code}"
    OXLINT = "https://oxc.rs/docs/guide/usage/linter/rules/{code}"
    PYDOCLINT = "https://jsh9.github.io/pydoclint/how_to_config.html"
    RUFF = "https://docs.astral.sh/ruff/rules/{code}/"
    SEMGREP = "https://semgrep.dev/r/{code}"
    SHELLCHECK = "https://www.shellcheck.net/wiki/{code}"
    SQLFLUFF = "https://docs.sqlfluff.com/en/stable/rules.html#{code}"
    STYLELINT = "https://stylelint.io/user-guide/rules/{code}"
    TAPLO = "https://taplo.tamasfe.dev/"
    TSC = "https://typescript.tv/errors/#ts{code}"
    YAMLLINT = "https://yamllint.readthedocs.io/en/stable/rules.html#{code}"
