"""Ratchet: nothing under ``lintro/ai`` may name a default provider.

lintro is provider-agnostic by construction (#2143): a user names
``anthropic``, ``cursor`` or ``openai`` and every surface treats the three as
equals. The failure mode this module guards is not a wrong default but *any*
default — a parameter default, a ``Field(default=...)``, an ``or "anthropic"``
fallback or a ``.get(key, "openai")`` that quietly picks a vendor when the user
did not, turning a missing setting into a silent choice instead of the
actionable "choose one of: …" error.

The scan is deliberately structural rather than a plain grep: shared modules
legitimately *mention* provider names in prose and in per-provider dispatch
tables, and a text ratchet would either flag those or be widened until it
caught nothing. Only default-shaped bindings are rejected. The allowlist is the
per-provider packages (``lintro/ai/providers/<name>/``) — a vendor package
naming its own vendor is inherent, not a default — and nothing else: every
other module under ``lintro/ai`` is scanned.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.provider_enum import (
    AIProvider,
    accepted_provider_names,
    accepted_provider_values,
)

_AI_PACKAGE = Path(__file__).resolve().parents[3] / "lintro" / "ai"

#: CLI modules outside ``lintro/ai`` that declare a provider-selecting option;
#: a click ``default="anthropic"`` creeping back there would bypass the
#: package-level scan.
_CLI_PACKAGE = _AI_PACKAGE.parent / "cli_utils"


def _cli_provider_modules() -> list[Path]:
    """Return every CLI module that declares a provider-selecting option.

    Discovered by scanning ``lintro/cli_utils`` for ``"--provider"`` rather
    than listed by hand, so a new command that grows the option is covered
    without editing this ratchet.

    Returns:
        Sorted paths of the CLI modules mentioning the ``--provider`` flag.
    """
    return sorted(
        path
        for path in _CLI_PACKAGE.rglob("*.py")
        if '"--provider"' in path.read_text(encoding="utf-8")
    )


#: The whole allowlist: per-provider packages, where naming the vendor the
#: package implements is inherent rather than a default.
_PROVIDER_PACKAGE_ROOTS = frozenset(
    f"lintro/ai/providers/{provider.value}/" for provider in AIProvider
)

_PROVIDER_NAMES = frozenset(provider.value for provider in AIProvider)


def _relative(path: Path) -> str:
    """Return a repo-relative POSIX path for reporting.

    Args:
        path: Absolute path to a scanned module.

    Returns:
        The path relative to the repository root, POSIX-separated.
    """
    return path.relative_to(_AI_PACKAGE.parents[1]).as_posix()


def _scanned_modules() -> list[Path]:
    """Return the modules subject to the ratchet.

    Returns:
        Every Python module under ``lintro/ai`` except the per-provider
        packages, plus the CLI modules that declare a provider option.
    """
    ai_modules = [
        path
        for path in sorted(_AI_PACKAGE.rglob("*.py"))
        if not any(_relative(path).startswith(root) for root in _PROVIDER_PACKAGE_ROOTS)
    ]
    return [*ai_modules, *_cli_provider_modules()]


def _is_provider_literal(node: ast.expr) -> bool:
    """Report whether *node* names a concrete provider.

    Args:
        node: Expression appearing in a default-shaped position.

    Returns:
        True for ``"anthropic"``-style string constants and for
        ``AIProvider.ANTHROPIC``-style enum member references.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.lower() in _PROVIDER_NAMES
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id == "AIProvider" and node.attr.lower() in _PROVIDER_NAMES
    return False


def _default_shaped_values(tree: ast.AST) -> list[tuple[int, str, ast.expr]]:
    """Collect every expression bound in a default-shaped position.

    Args:
        tree: Parsed module.

    Returns:
        ``(line, shape, expression)`` triples, where *shape* names the kind of
        default so a failure message can point at it.
    """
    found: list[tuple[int, str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arguments):
            candidates = [*node.defaults, *node.kw_defaults]
            found.extend(
                (value.lineno, "parameter default", value)
                for value in candidates
                if value is not None
            )
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            found.extend(
                (value.lineno, "`or` fallback", value) for value in node.values
            )
        elif isinstance(node, ast.IfExp):
            found.append((node.lineno, "conditional fallback", node.body))
            found.append((node.lineno, "conditional fallback", node.orelse))
        elif isinstance(node, ast.If):
            found.extend(_unset_guard_defaults(node=node))
        elif isinstance(node, ast.Match):
            found.extend(_match_arm_defaults(node=node))
        elif isinstance(node, ast.Call):
            found.extend(_call_defaults(node=node))
        elif isinstance(node, ast.Assign):
            if any(_names_a_default(target=target) for target in node.targets):
                found.extend(
                    (node.lineno, "default constant", value)
                    for value in _unpack_containers(value=node.value)
                )
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _names_a_default(target=node.target):
                found.extend(
                    (node.lineno, "default constant", value)
                    for value in _unpack_containers(value=node.value)
                )
    return found


def _names_a_default(*, target: ast.expr) -> bool:
    """Report whether an assignment target reads as a default.

    A subscript or attribute target is as much a default as a bare name —
    ``DEFAULTS["provider"] = "anthropic"`` binds one just as plainly as
    ``DEFAULT_PROVIDER = "anthropic"`` — so the printed target is matched
    rather than only ``ast.Name.id``.

    Args:
        target: Assignment target.

    Returns:
        True when the printed target contains ``default``.
    """
    if not isinstance(target, ast.Name | ast.Attribute | ast.Subscript):
        return False
    return "default" in ast.unparse(target).lower()


def _unpack_containers(*, value: ast.expr) -> list[ast.expr]:
    """Return *value* together with the members of any container it builds.

    A default is no less a default for sitting inside a literal container:
    ``DEFAULTS = {"provider": "anthropic"}`` and
    ``DEFAULT_ORDER = ("anthropic", "cursor")`` both name a vendor. Dict
    **keys** are deliberately not unpacked — a table keyed by provider is
    per-provider dispatch, not a default.

    Args:
        value: Expression bound in a default-shaped position.

    Returns:
        *value* and, recursively, the values of dict literals and the
        elements of tuple/list/set literals.
    """
    found = [value]
    if isinstance(value, ast.Dict):
        members: list[ast.expr] = [item for item in value.values if item is not None]
    elif isinstance(value, ast.Tuple | ast.List | ast.Set):
        members = list(value.elts)
    else:
        return found
    for member in members:
        found.extend(_unpack_containers(value=member))
    return found


def _unset_name(*, test: ast.expr) -> str | None:
    """Return the name an ``if`` tests for being unset, when it does.

    Recognises the three ways the imperative fallback is written:
    ``if provider is None``, ``if provider == None`` and ``if not provider``.

    Args:
        test: The ``if`` statement's test expression.

    Returns:
        The tested name, or None when the test is not an unset guard.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return test.operand.id if isinstance(test.operand, ast.Name) else None
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    if not isinstance(test.ops[0], ast.Is | ast.Eq):
        return None
    if not isinstance(test.left, ast.Name):
        return None
    comparator = test.comparators[0]
    is_none = isinstance(comparator, ast.Constant) and comparator.value is None
    return test.left.id if is_none else None


def _unset_guard_defaults(*, node: ast.If) -> list[tuple[int, str, ast.expr]]:
    """Collect the imperative form of a conditional fallback.

    ``ast.IfExp`` covers ``x = "anthropic" if x is None else x``; this covers
    the statement it desugars from::

        if provider is None:
            provider = "anthropic"

    Only an assignment back to the *same* name the test guards counts, so an
    unrelated binding inside a conditional is not read as a default.

    Args:
        node: An ``if`` statement.

    Returns:
        ``(line, shape, expression)`` triples for the guarded assignments.
    """
    name = _unset_name(test=node.test)
    if name is None:
        return []
    found: list[tuple[int, str, ast.expr]] = []
    for statement in node.body:
        if not isinstance(statement, ast.Assign):
            continue
        targets = [
            target for target in statement.targets if isinstance(target, ast.Name)
        ]
        if any(target.id == name for target in targets):
            found.append((statement.lineno, "unset-guard fallback", statement.value))
    return found


def _match_arm_defaults(*, node: ast.Match) -> list[tuple[int, str, ast.expr]]:
    """Collect values assigned by a ``match`` statement's catch-all arm.

    Only the wildcard ``case _:`` and ``case None:`` arms are inspected. Those
    are the default-shaped positions; a arm matching a concrete provider is
    per-provider dispatch, and reading its body would flag every branch of a
    dispatch table.

    Args:
        node: A ``match`` statement.

    Returns:
        ``(line, shape, expression)`` triples for the catch-all assignments.
    """
    found: list[tuple[int, str, ast.expr]] = []
    for case in node.cases:
        pattern = case.pattern
        is_wildcard = isinstance(pattern, ast.MatchAs) and pattern.pattern is None
        is_none = isinstance(pattern, ast.MatchSingleton) and pattern.value is None
        if not (is_wildcard or is_none) or case.guard is not None:
            continue
        for statement in case.body:
            if not isinstance(statement, ast.Assign | ast.AnnAssign):
                continue
            value = statement.value
            if value is None:
                continue
            found.append((statement.lineno, "match fallback", value))
    return found


def _call_defaults(*, node: ast.Call) -> list[tuple[int, str, ast.expr]]:
    """Collect default-shaped arguments of a call.

    Args:
        node: Call expression.

    Returns:
        ``(line, shape, expression)`` triples for ``Field(default=...)`` and
        pydantic's positional ``Field("openai")`` form, ``os.environ.get`` /
        ``os.getenv`` / ``dict.setdefault`` two-argument lookups, three-argument
        ``getattr`` and any
        keyword literally named ``default``.
    """
    found: list[tuple[int, str, ast.expr]] = []
    for keyword in node.keywords:
        if keyword.arg is not None and keyword.arg.lower().startswith("default"):
            found.append((node.lineno, f"`{keyword.arg}=` argument", keyword.value))
    func = node.func
    is_field = (isinstance(func, ast.Name) and func.id == "Field") or (
        isinstance(func, ast.Attribute) and func.attr == "Field"
    )
    if is_field and node.args:
        found.append((node.lineno, "positional `Field()` default", node.args[0]))
    # ``getattr`` takes the fallback third, after the object and the attribute
    # name; ``.get``/``getenv`` take it second, after the key.
    is_getattr = isinstance(func, ast.Name) and func.id == "getattr"
    if is_getattr:
        if len(node.args) >= 3:
            found.append((node.lineno, "lookup fallback", node.args[2]))
        return found
    is_get = isinstance(func, ast.Attribute) and func.attr in {
        "get",
        "getenv",
        "setdefault",
    }
    is_getenv = isinstance(func, ast.Name) and func.id == "getenv"
    if (is_get or is_getenv) and len(node.args) >= 2:
        found.append((node.lineno, "lookup fallback", node.args[1]))
    return found


@pytest.mark.parametrize(
    "module",
    _scanned_modules(),
    ids=_relative,
)
def test_no_module_defaults_to_a_provider(module: Path) -> None:
    """Assert no shared AI module binds a provider name as a default.

    Args:
        module: Module under ``lintro/ai`` outside the per-provider packages.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    violations = [
        f"{_relative(module)}:{line}: {shape} names a provider"
        for line, shape, value in _default_shaped_values(tree)
        if _is_provider_literal(value)
    ]
    assert_that(violations).described_as(
        "a provider named in a default-shaped position is an implicit default; "
        "resolve the provider explicitly or raise with accepted_provider_values()",
    ).is_empty()


@pytest.mark.parametrize(
    "source",
    [
        'def build(provider: str = "anthropic") -> None: ...',
        "def build(*, provider: str = AIProvider.OPENAI) -> None: ...",
        'provider = config.provider or "cursor"',
        'provider = os.environ.get("LINTRO_AI_PROVIDER", "anthropic")',
        'provider = getattr(cfg, "provider", "anthropic")',
        'provider = settings.setdefault("provider", "cursor")',
        'provider = Field(default="openai")',
        'provider = Field("openai")',
        'provider = pydantic.Field("openai")',
        'DEFAULT_PROVIDER = "cursor"',
        'DEFAULTS = {"provider": "anthropic"}',
        'DEFAULT_ORDER = ("cursor", "openai")',
        'DEFAULTS["provider"] = "anthropic"',
        'DEFAULT_PROVIDER: str = "cursor"',
        'provider = explicit if explicit else "anthropic"',
        'if provider is None:\n    provider = "anthropic"',
        'if not provider:\n    provider = "cursor"',
        'if provider == None:\n    provider = "openai"',
        'match provider:\n    case _:\n        provider = "anthropic"',
        'match provider:\n    case None:\n        provider = "cursor"',
        'provider = "anthropic" if provider is None else provider',
        'provider = explicit or "cursor" or fallback',
    ],
    ids=[
        "parameter-default",
        "keyword-only-enum-default",
        "or-fallback",
        "env-lookup-fallback",
        "getattr-fallback",
        "setdefault-fallback",
        "pydantic-field-default",
        "pydantic-field-positional-default",
        "pydantic-qualified-field-positional-default",
        "default-constant",
        "default-constant-in-a-dict",
        "default-constant-in-a-tuple",
        "default-constant-via-a-subscript-target",
        "annotated-default-constant",
        "conditional-fallback",
        "unset-guard-is-none",
        "unset-guard-falsy",
        "unset-guard-equals-none",
        "match-wildcard-arm",
        "match-none-arm",
        "conditional-fallback-in-body",
        "or-fallback-mid-chain",
    ],
)
def test_ratchet_catches_a_default_provider(source: str) -> None:
    """Assert the scan flags each default shape it claims to cover.

    A ratchet nobody has seen fail is a ratchet that may be scanning nothing,
    so each supported shape is exercised against a synthetic module.

    Args:
        source: A one-line module that binds a provider as a default.
    """
    flagged = [
        shape
        for _, shape, value in _default_shaped_values(ast.parse(source))
        if _is_provider_literal(value)
    ]
    assert_that(flagged).described_as(source).is_not_empty()


@pytest.mark.parametrize(
    "source",
    [
        'provider = getattr(cfg, "provider")',
        'provider = getattr(cfg, "anthropic")',
    ],
    ids=["getattr-without-a-fallback", "getattr-whose-attribute-is-a-vendor"],
)
def test_ratchet_ignores_a_getattr_with_no_fallback(source: str) -> None:
    """Assert a two-argument ``getattr`` is not read as a default.

    ``getattr`` puts its fallback third, so the second argument is the
    attribute name. Reading it as the fallback both missed real defaults and
    would fire on an attribute that happens to be named for a vendor.

    Args:
        source: A one-line module with a ``getattr`` that has no fallback.
    """
    flagged = [
        shape
        for _, shape, value in _default_shaped_values(ast.parse(source))
        if _is_provider_literal(value)
    ]
    assert_that(flagged).described_as(source).is_empty()


@pytest.mark.parametrize(
    "source",
    [
        'match provider:\n    case "anthropic":\n        binary = "anthropic"',
        'if provider is None:\n    transport = "anthropic"',
    ],
    ids=["match-arm-for-a-concrete-provider", "unset-guard-binding-another-name"],
)
def test_ratchet_ignores_dispatch_inside_conditionals(source: str) -> None:
    """Assert per-provider dispatch is not read as a fallback.

    Only a catch-all ``match`` arm and an assignment back to the guarded name
    are defaults. Reading every arm of a dispatch table, or every binding
    under any conditional, would flag the code that exists precisely because
    lintro treats the providers as equals.

    Args:
        source: A one-line module whose provider name is dispatch, not a
            default.
    """
    flagged = [
        shape
        for _, shape, value in _default_shaped_values(ast.parse(source))
        if _is_provider_literal(value)
    ]
    assert_that(flagged).described_as(source).is_empty()


def test_ratchet_ignores_provider_names_outside_defaults() -> None:
    """Assert prose and per-provider dispatch are not flagged.

    Shared modules must stay free to *mention* vendors — the ratchet targets
    defaults, and one that fired on every mention would be widened away.
    """
    source = (
        '"""Talks to anthropic, cursor and openai."""\n'
        'TAXONOMY = {"anthropic": (), "cursor": (), "openai": ()}\n'
        'DEFAULT_TIMEOUTS = {"anthropic": 30, "cursor": 30, "openai": 30}\n'
        "def is_bare(provider): return provider is AIProvider.ANTHROPIC\n"
    )
    flagged = [
        shape
        for _, shape, value in _default_shaped_values(ast.parse(source))
        if _is_provider_literal(value)
    ]
    assert_that(flagged).is_empty()


def test_scan_covers_the_shared_ai_modules() -> None:
    """Assert the allowlist did not swallow the surface under audit."""
    scanned = {_relative(path) for path in _scanned_modules()}
    assert_that(scanned).contains(
        "lintro/ai/config.py",
        "lintro/ai/providers/__init__.py",
        "lintro/ai/registry.py",
        "lintro/ai/availability.py",
    )
    assert_that(scanned).does_not_contain(
        "lintro/ai/providers/anthropic/metadata.py",
    )


def test_config_has_no_provider_default() -> None:
    """Assert AIConfig leaves the provider unset rather than picking one."""
    assert_that(AIConfig().provider).is_none()


def test_provider_enum_is_declared_alphabetically() -> None:
    """Assert declaration order carries no ranking.

    Declaration order is the iteration order of the plugin registry, the
    metadata facade, the doctor/status panels and the generated docs tables, so
    it is the one place a preference could leak in.
    """
    declared = [provider.value for provider in AIProvider]
    assert_that(declared).is_equal_to(sorted(declared))


def test_accepted_provider_helpers_agree_and_list_every_provider() -> None:
    """Assert the user-facing enumeration is complete and alphabetical."""
    names = accepted_provider_names()
    assert_that(names).is_equal_to(sorted(provider.value for provider in AIProvider))
    assert_that(accepted_provider_values()).is_equal_to(", ".join(names))


def test_cli_provider_modules_are_discovered_not_listed() -> None:
    """The review command is found by scanning, so a new command is covered too."""
    discovered = {path.name for path in _cli_provider_modules()}

    assert_that(discovered).contains("review.py")
    assert_that(_cli_provider_modules()).is_not_empty()
