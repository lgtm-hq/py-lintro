"""Discover and resolve Semgrep packs shipped with this Lintro version."""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from lintro.exceptions.errors import ConfigurationError

_RULES_ROOT = Path(__file__).parent


@dataclass(frozen=True)
class RulePackInfo:
    """Facts Lintro reads from a shipped pack's YAML.

    Attributes:
        rule_ids: Rule IDs the shipped YAML defines.
        discover_all_files: Whether a rule sets ``lintro_discover_all_files``
            metadata, asking Lintro to hand Semgrep every candidate file so the
            rule's ``paths.include`` globs, not file extensions, pick targets.
        anchored_directories: Directories named by ``paths.include`` globs, such
            as ``scripts/`` from ``**/scripts/**``. Semgrep matches them against
            paths relative to the scan root, so they need a project marker to
            anchor that root. Empty when no glob names a directory.
    """

    rule_ids: frozenset[str]
    discover_all_files: bool
    anchored_directories: tuple[str, ...]


def available_rule_packs() -> list[str]:
    """List packaged language/pack names in stable order.

    Returns:
        Names accepted by the Semgrep ``rule_packs`` option.
    """
    return sorted(
        path.relative_to(_RULES_ROOT).with_suffix("").as_posix()
        for path in _RULES_ROOT.glob("*/*.yaml")
        if path.is_file()
    )


def resolve_rule_pack(name: str) -> Path:
    """Resolve a known pack to an absolute installed package path.

    Args:
        name: Language/pack name, without a file extension.

    Returns:
        Local YAML path suitable for Semgrep's ``--config`` option.

    Raises:
        ConfigurationError: If the pack is unknown, including arbitrary paths.
    """
    known = available_rule_packs()
    if name not in known:
        raise ConfigurationError(
            f"Unknown Semgrep rule pack {name!r}. "
            f"Available packs: {', '.join(known)}. See 'lintro list-tools --rules'.",
        )
    return (_RULES_ROOT / f"{name}.yaml").resolve()


def validate_rule_packs(value: object) -> list[str]:
    """Validate pack names and remove duplicates without reordering them.

    Args:
        value: User-supplied option, which must be a list of pack names.

    Returns:
        Validated names in the user's order.

    Raises:
        ConfigurationError: If the value has an invalid type or unknown name.
    """
    if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
        raise ConfigurationError("semgrep.rule_packs must be a list of pack names")
    names: list[str] = []
    for name in value:
        resolve_rule_pack(name)
        if name not in names:
            names.append(name)
    return names


def rule_pack_info(name: str) -> RulePackInfo:
    """Read a known pack's rule IDs and Lintro metadata, loading it once.

    Args:
        name: Language/pack name, without a file extension.

    Returns:
        The pack's rule IDs and the flags Lintro acts on.
    """
    return _load_rule_pack(resolve_rule_pack(name))


@cache
def _load_rule_pack(path: Path) -> RulePackInfo:
    """Parse one installed pack file; shipped packs do not change at runtime.

    Args:
        path: Resolved pack YAML path, which also keys the cache.

    Returns:
        The pack's rule IDs and the flags Lintro acts on.
    """
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]
    includes = [
        glob for rule in rules for glob in rule.get("paths", {}).get("include", [])
    ]
    return RulePackInfo(
        rule_ids=frozenset(rule["id"] for rule in rules),
        discover_all_files=any(
            rule.get("metadata", {}).get("lintro_discover_all_files") is True
            for rule in rules
        ),
        anchored_directories=tuple(
            dict.fromkeys(
                glob.removeprefix("**/").removesuffix("**")
                for glob in includes
                if "/" in glob.removeprefix("**/")
            ),
        ),
    )
