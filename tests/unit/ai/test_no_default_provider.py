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
_CLI_PROVIDER_MODULES = (_AI_PACKAGE.parent / "cli_utils" / "commands" / "review.py",)

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
    return [*ai_modules, *_CLI_PROVIDER_MODULES]


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
        elif isinstance(node, ast.Call):
            found.extend(_call_defaults(node=node))
        elif isinstance(node, ast.Assign):
            names = [
                target.id for target in node.targets if isinstance(target, ast.Name)
            ]
            if any("default" in name.lower() for name in names):
                found.append((node.lineno, "default constant", node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if (
                isinstance(node.target, ast.Name)
                and "default" in node.target.id.lower()
            ):
                found.append((node.lineno, "default constant", node.value))
    return found


def _call_defaults(*, node: ast.Call) -> list[tuple[int, str, ast.expr]]:
    """Collect default-shaped arguments of a call.

    Args:
        node: Call expression.

    Returns:
        ``(line, shape, expression)`` triples for ``Field(default=...)`` and
        pydantic's positional ``Field("openai")`` form, ``os.environ.get`` /
        ``os.getenv`` two-argument lookups, three-argument ``getattr`` and any
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
    is_get = isinstance(func, ast.Attribute) and func.attr in {"get", "getenv"}
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
        'provider = Field(default="openai")',
        'provider = Field("openai")',
        'provider = pydantic.Field("openai")',
        'DEFAULT_PROVIDER = "cursor"',
        'provider = explicit if explicit else "anthropic"',
        'provider = "anthropic" if provider is None else provider',
        'provider = explicit or "cursor" or fallback',
    ],
    ids=[
        "parameter-default",
        "keyword-only-enum-default",
        "or-fallback",
        "env-lookup-fallback",
        "getattr-fallback",
        "pydantic-field-default",
        "pydantic-field-positional-default",
        "pydantic-qualified-field-positional-default",
        "default-constant",
        "conditional-fallback",
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


def test_ratchet_ignores_provider_names_outside_defaults() -> None:
    """Assert prose and per-provider dispatch are not flagged.

    Shared modules must stay free to *mention* vendors — the ratchet targets
    defaults, and one that fired on every mention would be widened away.
    """
    source = (
        '"""Talks to anthropic, cursor and openai."""\n'
        'TAXONOMY = {"anthropic": (), "cursor": (), "openai": ()}\n'
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
