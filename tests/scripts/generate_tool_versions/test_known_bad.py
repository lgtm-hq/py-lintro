"""The generator rejects a known-bad entry the runtime could not apply (#3556).

An entry lintro cannot read would refuse nothing at run time, so generating
the manifest from it must fail with exit 2 and name the tool and the entry.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

_GOOD: dict[str, str] = {
    "versions": "==0.43.1",
    "reason": "drops findings",
    "reference": "https://example.invalid/bad",
}


def _set_known_bad(fake_repo: Path, value: Any) -> None:
    """Give the fake repo's first tool (oxfmt) a ``known_bad_versions`` value.

    Args:
        fake_repo: Fake repo fixture root.
        value: The value to store under ``known_bad_versions``.
    """
    src_path = fake_repo / "lintro" / "tools" / "manifest.src.json"
    data = json.loads(src_path.read_text())
    data["tools"][0]["known_bad_versions"] = value
    src_path.write_text(json.dumps(data, indent=2) + "\n")


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ([{**_GOOD, "versions": "2.6.5"}], "must be comma-separated clauses"),
        ([{**_GOOD, "versions": "==2.6.5 or so"}], "must be comma-separated clauses"),
        ([{"versions": "==2.6.5", "reference": "x"}], "missing keys ['reason']"),
        (
            [{"versions": "==2.6.5", "reasons": "typo", "reference": "x"}],
            "unknown keys ['reasons']",
        ),
        ([{**_GOOD, "reason": " "}], "'reason' must be a non-empty string"),
        ("==2.6.5", "known_bad_versions must be a list, got str"),
        (["==2.6.5"], "an entry must be an object"),
    ],
    ids=[
        "bare-version",
        "trailing-text",
        "missing-reason",
        "misspelled-key",
        "blank-reason",
        "non-list",
        "non-object-entry",
    ],
)
def test_invalid_known_bad_entry_exits_two(
    retargeted_gen: SimpleNamespace,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
    value: Any,
    problem: str,
) -> None:
    """Each malformed shape fails generation with a message naming the tool.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
        capsys: Pytest stdout/stderr capture.
        value: The malformed ``known_bad_versions`` value.
        problem: Text the error must contain.
    """
    _set_known_bad(fake_repo, value)

    rc = retargeted_gen.main([])

    assert_that(rc).is_equal_to(retargeted_gen.EXIT_INPUT_ERROR)
    assert_that(capsys.readouterr().err).contains("'oxfmt'", problem)
    assert_that(retargeted_gen.paths.manifest_path.exists()).is_false()


@pytest.mark.parametrize(
    "versions",
    ["==2.7.0", ">=2.4.0,<2.6.0", "==2.7.*", "!=1.0, <0.9"],
)
def test_valid_known_bad_entry_is_written_through(
    retargeted_gen: SimpleNamespace,
    fake_repo: Path,
    versions: str,
) -> None:
    """A valid entry generates, and reaches manifest.json unchanged.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
        versions: A ``versions`` string in the known-bad grammar.
    """
    entry = {**_GOOD, "versions": versions}
    _set_known_bad(fake_repo, [entry])

    rc = retargeted_gen.main([])

    assert_that(rc).is_equal_to(retargeted_gen.EXIT_OK)
    manifest = json.loads(retargeted_gen.paths.manifest_path.read_text())
    assert_that(manifest["tools"][0]["known_bad_versions"]).is_equal_to([entry])
