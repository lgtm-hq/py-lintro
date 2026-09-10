"""Contract tests for the promote-time manifest staleness input set (#2497).

The guard in ``scripts/ci/check-tools-manifest-staleness.sh`` refuses a
tools-image candidate whose manifest inputs no longer match main. Its path
list therefore has to stay identical to the inputs the version generator
renders ``lintro/tools/manifest.json`` from — the manifest the
image-vs-manifest gate checks the image against. Both sides are read from
source here so drift fails a test rather than silently narrowing the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

from assertpy import assert_that

from lintro_build.versions.generate import REQUIREMENTS_PYPI_SOURCES
from lintro_build.versions.paths import GeneratorPaths

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARD_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "check-tools-manifest-staleness.sh"

# GeneratorPaths fields that name a file the generator *reads*. Everything the
# generator writes is derived, so it is deliberately out of the guard's set.
_GENERATOR_INPUT_FIELDS = frozenset(
    {
        "seed_path",
        "tool_versions_path",
        "package_json_path",
        "pyproject_path",
        "manifest_src_path",
    },
)
_GENERATOR_OUTPUT_FIELDS = frozenset({"manifest_path", "generated_path"})
# The image build recipe is not a manifest input, but a candidate built from a
# different tools.Dockerfile is just as stale, so the guard watches it too.
_GUARD_ONLY_PATHS = frozenset({"docker/tools.Dockerfile"})


def _guard_paths() -> set[str]:
    """Return the guard's default manifest path list, read from its source.

    Returns:
        Repository-relative paths listed in ``DEFAULT_MANIFEST_PATHS``.
    """
    source = _GUARD_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"^DEFAULT_MANIFEST_PATHS='\n(?P<paths>.*?)'$",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert_that(match).is_not_none()
    assert match is not None  # narrow for mypy
    return {line.strip() for line in match.group("paths").splitlines() if line.strip()}


def _generator_input_paths() -> set[str]:
    """Return every committed file the rendered manifest is generated from.

    Returns:
        Repository-relative paths the version generator reads.
    """
    paths = GeneratorPaths.from_repo_root(_REPO_ROOT)
    inputs = {
        str(getattr(paths, field).relative_to(_REPO_ROOT))
        for field in _GENERATOR_INPUT_FIELDS
    }
    return inputs | set(REQUIREMENTS_PYPI_SOURCES.values())


def test_generator_paths_fields_are_all_classified() -> None:
    """A new generator path must be classified before the guard can be trusted.

    ``_GENERATOR_INPUT_FIELDS`` is hand-maintained. If the generator grows a
    new input, this fails first and points at the classification, so the guard
    list below cannot quietly fall behind.
    """
    fields = set(GeneratorPaths.__dataclass_fields__)
    assert_that(fields).is_equal_to(
        {"repo_root"} | set(_GENERATOR_INPUT_FIELDS) | set(_GENERATOR_OUTPUT_FIELDS),
    )


def test_guard_watches_every_rendered_manifest_input() -> None:
    """The guard's path list must equal the generator inputs plus the recipe."""
    assert_that(_guard_paths()).is_equal_to(
        _generator_input_paths() | set(_GUARD_ONLY_PATHS),
    )


def test_guard_paths_exist_in_the_repository() -> None:
    """Every watched path must be a real committed file, not a typo."""
    for path in sorted(_guard_paths()):
        assert_that((_REPO_ROOT / path).is_file()).described_as(path).is_true()
