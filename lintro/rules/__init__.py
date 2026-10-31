"""Discover and resolve Semgrep packs shipped with this Lintro version."""

from pathlib import Path

from lintro.exceptions.errors import ConfigurationError

_RULES_ROOT = Path(__file__).parent


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
