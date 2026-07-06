"""Runtime loader for packaged AI prompt templates.

Prompt bodies live as data files under ``lintro/ai/prompts/templates`` and are
loaded verbatim at import time via :func:`load_prompt_template`. Keeping the
copy out of Python modules yields cleaner diffs, drops ``E501`` exemptions for
long production prompts, and gives every current and future prompt one loading
pattern. The returned text is byte-for-byte identical to the previous inline
constants, so ``.format()`` placeholders and ``{{`` / ``}}`` escaping survive
unchanged.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

_TEMPLATES_PACKAGE = "lintro.ai.prompts.templates"


@lru_cache(maxsize=None)
def load_prompt_template(*path_parts: str) -> str:
    """Load a packaged prompt template as UTF-8 text.

    Args:
        *path_parts: Path segments under the templates package, e.g.
            ``("review", "system.md")``. Segments are joined with the
            package resource root.

    Returns:
        The template contents decoded as UTF-8, verbatim.

    Raises:
        ValueError: If no path parts are supplied.
        FileNotFoundError: If the resolved template does not exist.
    """
    if not path_parts:
        raise ValueError("load_prompt_template requires at least one path part")
    resource = files(_TEMPLATES_PACKAGE).joinpath(*path_parts)
    if not resource.is_file():
        joined = "/".join(path_parts)
        raise FileNotFoundError(
            f"Prompt template not found: {joined} "
            f"(package {_TEMPLATES_PACKAGE})",
        )
    return resource.read_text(encoding="utf-8")
