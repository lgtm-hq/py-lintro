"""Self-check that syntax highlighting still works in a frozen binary.

The release binaries ship ``pygments`` as bytecode rather than compiled C
(#2484): 321 of the ~1500 generated C units were pygments, 260 of them
individual language lexers, all for the one ``rich.syntax`` call site in
:mod:`lintro.ai.interactive`, which asks for the ``diff`` lexer by name.
Nuitka already treats ``rich`` itself that way.

Bytecode inclusion keeps the modules in the binary, but the lexers are
resolved *by name* at runtime, so a packaging mistake there would not show up
in ``--version``, ``--help`` or the tool-registry smoke test — it would
surface as unhighlighted output, or an exception, the first time a user
reviewed an AI fix. ``scripts/build/verify_built_binary.sh`` runs this check
against the built binary so the dynamic lookup is proven at build time.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Languages the check resolves. ``diff`` comes first because it is the only
#: lexer the binary actually asks for: ``lintro/ai/interactive.py`` renders
#: every AI fix with ``Syntax(fix.diff, "diff", ...)``, and that is the whole
#: of pygments' use in the product. The other three are breadth - lexers from
#: other pygments modules, so a lookup failure that is not specific to
#: ``diff`` is caught too.
CHECKED_LANGUAGES: tuple[str, ...] = ("diff", "python", "yaml", "json")

#: Snippet per language, chosen to produce at least one keyword/string token.
_SNIPPETS: dict[str, str] = {
    "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old = 1\n+new = 2\n",
    "python": 'def greet(name: str) -> str:\n    return f"hi {name}"\n',
    "yaml": "name: lintro\nversions:\n  - '1'\n",
    "json": '{"name": "lintro", "ok": true}\n',
}

#: Lexer class name that means nothing was recognised. pygments itself
#: raises ``ClassNotFound`` for an alias it cannot resolve (handled by the
#: lookup branch below); it is ``rich.syntax`` that substitutes ``TextLexer``
#: when it catches that. So this guard is defence in depth for the shipped
#: languages, and the live branch for a literal ``text`` language.
_FALLBACK_LEXER = "TextLexer"


@dataclass(frozen=True)
class HighlightingCheckResult:
    """Outcome of the syntax-highlighting self-check.

    Attributes:
        ok: Whether every checked language highlighted successfully.
        details: One human-readable line per checked language.
        failures: Messages for the languages that failed, empty when ``ok``.
    """

    ok: bool
    details: tuple[str, ...]
    failures: tuple[str, ...]


def check_syntax_highlighting(
    *,
    languages: tuple[str, ...] = CHECKED_LANGUAGES,
) -> HighlightingCheckResult:
    """Resolve a pygments lexer per language and highlight a snippet with it.

    Both halves matter: resolving the lexer proves the dynamic, by-name module
    lookup still works from bytecode, and highlighting proves the lexer
    actually tokenizes rather than silently degrading to the plain-text
    fallback.

    Args:
        languages: Language names to check.

    Returns:
        A :class:`HighlightingCheckResult` describing every language checked.
    """
    from pygments.lexers import get_lexer_by_name
    from rich.syntax import Syntax

    details: list[str] = []
    failures: list[str] = []
    for language in languages:
        # Every name in CHECKED_LANGUAGES has an entry; a test asserts the two
        # key sets are equal. The default serves ad-hoc ``languages=`` callers.
        snippet = _SNIPPETS.get(language, "x = 1\n")
        try:
            lexer = get_lexer_by_name(language)
        except Exception as exc:
            failures.append(f"{language}: lexer lookup failed: {exc!r}")
            continue
        lexer_name = type(lexer).__name__
        if lexer_name == _FALLBACK_LEXER:
            failures.append(f"{language}: resolved the plain-text fallback lexer")
            continue
        try:
            highlighted = Syntax(snippet, language).highlight(snippet)
        except Exception as exc:
            failures.append(f"{language}: highlighting failed: {exc!r}")
            continue
        if not highlighted.spans:
            failures.append(f"{language}: highlighting produced no style spans")
            continue
        details.append(
            f"{language}: {lexer_name}, {len(highlighted.spans)} style spans",
        )
    return HighlightingCheckResult(
        ok=not failures,
        details=tuple(details),
        failures=tuple(failures),
    )
