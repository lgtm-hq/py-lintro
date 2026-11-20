"""The generator and the runtime enforce one known-bad grammar (#3556).

``versions`` in a known-bad entry is checked twice: by the stdlib-only
manifest generator (``lintro_build/versions/known_bad.py``) and by the runtime
(``KnownBadVersion``) before ``packaging`` parses it. When the two disagreed,
an entry the generator accepted crashed ``lintro versions``, ``doctor``,
``list-tools`` and ``install`` with a traceback. One shared list of strings
feeds both validators here, so they cannot drift apart again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from packaging.specifiers import SpecifierSet

from lintro.cli import cli
from lintro.tools.core import tool_registry
from lintro.tools.core.manifest_models import KnownBadVersion
from lintro.tools.core.tool_registry import ManifestRegistry
from lintro_build.versions.known_bad import versions_problem

#: ``versions`` strings both validators accept.
ACCEPTED: tuple[str, ...] = (
    "==2.7.0",
    "==2",
    "!=2.7.0",
    "<2.6.0",
    "<=2.6",
    ">2.4.0",
    ">=2.4.0,<2.6.0",
    ">=2.4.0, <2.6.0",
    "== 2.7.0",
    "==2.7.*",
    "!=2.*",
    "==10.20.30.40",
)

#: ``versions`` strings both validators reject.
REJECTED: tuple[str, ...] = (
    "~=2",
    "~=2.7.0",
    "<2.*",
    ">=2.*",
    "<=2.7.0.*",
    ">2.7.*",
    "==2.7.0rc1",
    "==2.7.0rc1rc2",
    "==2.7.0.post1",
    "==2.7.0.dev1",
    "==1!2.7.0",
    "==2.7.0+local",
    "==v2.7.0",
    "===2.7.0",
    "2.7.0",
    "==2.7.0,",
    ",==2.7.0",
    "==2..7",
    "==2.7.",
    "==2.*.0",
    "==٢.7.0",
    "==2.7.0 ",
    "==2.7.0 or so",
    "",
)


def _runtime_accepts(versions: str) -> bool:
    """Whether the runtime accepts ``versions`` in a known-bad entry.

    Args:
        versions: The entry's ``versions`` value.

    Returns:
        True when ``KnownBadVersion`` constructs.
    """
    try:
        KnownBadVersion(versions=versions, reason="r", reference="x")
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(
    ("versions", "accepted"),
    [(value, True) for value in ACCEPTED] + [(value, False) for value in REJECTED],
)
def test_generator_and_runtime_agree_on_the_grammar(
    versions: str,
    accepted: bool,
) -> None:
    """Both validators give the same verdict; ``packaging`` takes what they accept.

    Args:
        versions: A known-bad ``versions`` string.
        accepted: Whether the grammar accepts it.
    """
    generator_accepts = versions_problem(versions) is None

    assert_that(
        (generator_accepts, _runtime_accepts(versions)),
    ).is_equal_to((accepted, accepted))
    if accepted:
        assert_that(str(SpecifierSet(versions))).is_not_none()


def test_rejection_states_the_grammar() -> None:
    """Both rejections say what the grammar is."""
    with pytest.raises(ValueError, match="known-bad versions") as raised:
        KnownBadVersion(versions="~=2", reason="r", reference="x")

    for message in (str(raised.value), str(versions_problem("~=2"))):
        assert_that(message).contains(
            "'~=2' must be comma-separated clauses, each ==, !=, <, <=, > or >=",
            "trailing .* only after == or !=",
            "no ~=, pre-release, post-release, epoch or local parts",
        )


@pytest.mark.parametrize(
    "args",
    [
        ["versions"],
        ["doctor"],
        ["list-tools"],
        ["install", "--dry-run", "osv_scanner"],
    ],
    ids=["versions", "doctor", "list-tools", "install"],
)
def test_hand_broken_manifest_is_a_one_line_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    """A malformed entry edited into manifest.json fails with one line, no traceback.

    Args:
        tmp_path: Directory for the broken manifest.
        monkeypatch: Points the registry at it.
        args: The command line.
    """
    data = json.loads(tool_registry._MANIFEST_PATH.read_text(encoding="utf-8"))
    for tool in data["tools"]:
        if tool["name"] == "osv_scanner":
            tool["known_bad_versions"] = [
                {"versions": "~=2", "reason": "r", "reference": "https://x.invalid"},
            ]
    broken = tmp_path / "manifest.json"
    broken.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(tool_registry, "_MANIFEST_PATH", broken)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    ManifestRegistry.clear_cache()
    try:
        result = CliRunner().invoke(cli, args)
    finally:
        ManifestRegistry.clear_cache()

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).does_not_contain("Traceback")
    errors = [line for line in result.output.splitlines() if "Error:" in line]
    assert_that(errors).is_length(1)
    assert_that(errors[0]).starts_with(
        "Error: lintro's tool manifest is invalid: Tool 'osv_scanner': "
        "known_bad_versions entry {'versions': '~=2'",
    )
