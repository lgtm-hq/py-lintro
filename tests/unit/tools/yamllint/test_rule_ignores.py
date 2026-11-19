"""Rule-level yamllint ignores exempt a file from that rule only (#2400).

Runs the real yamllint, which the dev dependency group installs. yamllint
matches ``ignore`` patterns against the path exactly as it is passed, so lintro
must hand it a path relative to the config file rather than drop the file.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.base_issue import BaseIssue
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


_TOP_LEVEL_IGNORE = "extends: default\nignore: |\n  sub/long.yaml\n"


def _two_documents(directory: Path) -> tuple[Path, Path]:
    """Write one file a top-level ignore names and one it does not.

    Args:
        directory: Directory that receives ``sub/long.yaml`` and
            ``sub/other.yaml``.

    Returns:
        The ignored file and the linted one.
    """
    sub = directory / "sub"
    sub.mkdir(parents=True)
    ignored = sub / "long.yaml"
    other = sub / "other.yaml"
    for document in (ignored, other):
        document.write_text(_DOCUMENT)
    return ignored, other


def _reported_files(result_issues: Sequence[BaseIssue] | None) -> set[str]:
    """Return the names of files with findings.

    Args:
        result_issues: Issues from a ToolResult.

    Returns:
        Basenames of the reported files.
    """
    return {Path(issue.file).name for issue in result_issues or []}


def test_explicit_config_matches_paths_from_the_working_directory(
    yamllint_plugin: YamllintPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``config_file`` behaves like ``yamllint -c`` run from lintro's directory.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Project root lintro runs from.
        monkeypatch: Pytest fixture used to change directory.
    """
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "yl.yaml").write_text(_TOP_LEVEL_IGNORE)
    ignored, other = _two_documents(tmp_path)
    monkeypatch.chdir(tmp_path)
    yamllint_plugin.set_options(config_file="configs/yl.yaml")

    result = yamllint_plugin.check([str(ignored), str(other)], {})

    assert_that(_reported_files(result.issues)).is_equal_to({"other.yaml"})


def test_discovered_config_matches_paths_from_its_own_directory(
    yamllint_plugin: YamllintPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested discovered config applies its ignores relative to itself.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Project root lintro runs from.
        monkeypatch: Pytest fixture used to change directory.
    """
    package = tmp_path / "packages" / "a"
    package.mkdir(parents=True)
    (package / ".yamllint").write_text(_TOP_LEVEL_IGNORE)
    ignored, other = _two_documents(package)
    monkeypatch.chdir(tmp_path)

    result = yamllint_plugin.check([str(ignored), str(other)], {})

    assert_that(_reported_files(result.issues)).is_equal_to({"other.yaml"})


@pytest.mark.parametrize(
    "output_format",
    ["parsable", "standard", "colored", "github", "auto"],
)
def test_raw_output_names_the_reported_path(
    yamllint_plugin: YamllintPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    """Raw output in every format names the file as lintro reports it.

    yamllint runs from the config's directory on a relative path, but headers
    and annotations must not point at ``sub/bad.yaml`` under the project root.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Project root lintro runs from.
        monkeypatch: Pytest fixture used to change directory.
        output_format: yamllint output format under test.
    """
    package = tmp_path / "packages" / "a"
    (package / "sub").mkdir(parents=True)
    (package / ".yamllint").write_text("extends: default\n")
    bad = package / "sub" / "bad.yaml"
    bad.write_text("a:  1\n")
    monkeypatch.chdir(tmp_path)
    yamllint_plugin.set_options(format=output_format)

    result = yamllint_plugin.check([str(bad)], {})

    output = result.output or ""
    assert_that(output).contains(str(bad))
    assert_that(output.replace(str(bad), "")).does_not_contain("sub/bad.yaml")
