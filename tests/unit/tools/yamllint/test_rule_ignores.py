"""Rule-level yamllint ignores exempt a file from that rule only (#2400).

Runs the real yamllint, which the dev dependency group installs. yamllint
matches ``ignore`` patterns against the path exactly as it is passed, so lintro
must hand it a path relative to the config file rather than drop the file.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.yamllint.yamllint_issue import YamllintIssue
from lintro.tools.yamllint.definition import YamllintPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("yamllint") is None,
    reason="yamllint is not installed",
)

_CONFIG = """\
extends: default
rules:
  document-start: disable
  line-length:
    max: 20
    ignore: |
      sub/long.yaml
"""

_DOCUMENT = "key: this line is definitely longer than twenty\nbad:   value\n"


def test_rule_ignore_keeps_other_rules(
    yamllint_plugin: YamllintPlugin,
    tmp_path: Path,
) -> None:
    """A line-length ignore drops line-length findings only, for that file only.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Directory holding the config and YAML files.
    """
    (tmp_path / ".yamllint").write_text(_CONFIG)
    sub = tmp_path / "sub"
    sub.mkdir()
    ignored = sub / "long.yaml"
    ignored.write_text(_DOCUMENT)
    other = sub / "other.yaml"
    other.write_text(_DOCUMENT)

    result = yamllint_plugin.check([str(ignored), str(other)], {})

    found = sorted(
        (Path(issue.file).name, issue.rule)
        for issue in result.issues
        if isinstance(issue, YamllintIssue)
    )
    assert_that(found).is_equal_to(
        [
            ("long.yaml", "colons"),
            ("other.yaml", "colons"),
            ("other.yaml", "line-length"),
        ],
    )
    assert_that(
        [Path(issue.file).is_absolute() for issue in result.issues],
    ).is_equal_to(
        [True, True, True],
    )
