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
_TOOLS_DOCKERFILE = _REPO_ROOT / "docker" / "tools.Dockerfile"
_GENERATE_SCRIPT = "scripts/ci/generate-tool-versions.py"

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
# Not manifest *inputs*, but a candidate built from a different recipe or a
# different generator is just as stale: docker/tools.Dockerfile re-renders the
# manifest during the build with the generator code it copies in.
_GUARD_ONLY_PATHS = frozenset(
    {
        "docker/tools.Dockerfile",
        "lintro_build/",
        _GENERATE_SCRIPT,
    },
)


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
    """Every watched path must be a real committed file or directory."""
    for path in sorted(_guard_paths()):
        assert_that((_REPO_ROOT / path).exists()).described_as(path).is_true()


def _render_step_copy_sources(dockerfile: Path = _TOOLS_DOCKERFILE) -> list[str]:
    """Return the paths *dockerfile* copies in before rendering the manifest.

    Flags are skipped so a ``COPY --chmod=0755 src dst`` is parsed like any
    other; ``COPY --from=<stage>`` is dropped entirely because its sources
    name a build stage, not repository paths.

    Args:
        dockerfile: Dockerfile to parse (the tools image recipe by default).

    Returns:
        Repository-relative COPY sources preceding the ``RUN`` that invokes
        the version generator.
    """
    sources: list[str] = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("COPY "):
            parts = stripped.split()[1:]
            flags = [part for part in parts if part.startswith("--")]
            if any(flag.startswith("--from=") for flag in flags):
                continue
            operands = [part for part in parts if not part.startswith("--")]
            # The last operand is the destination inside the image.
            sources.extend(operands[:-1])
        elif _GENERATE_SCRIPT in stripped:
            break
    assert_that(sources).is_not_empty()
    return sources


def test_guard_covers_every_path_the_render_step_copies() -> None:
    """Everything the image re-renders the manifest from must be represented.

    The build copies these paths in and runs the generator, and the gates
    re-render with main's code, so a change to any of them can move the
    rendered manifest out from under a candidate. ``lintro/`` and ``scripts/``
    are copied wholesale while only some of their members are generator
    inputs, so the invariant is representation: every COPY source must be
    watched itself or contain a watched path. A brand-new COPY with nothing
    watched underneath fails here.
    """
    watched = _guard_paths()
    for source in _render_step_copy_sources():
        prefix = source if source.endswith("/") else f"{source}/"
        covered = any(
            path == source or path.startswith(prefix) or source.startswith(path)
            for path in watched
        )
        assert_that(covered).described_as(f"{source} is unwatched").is_true()


def test_copy_parser_reads_flagged_and_multi_source_copies(tmp_path: Path) -> None:
    """Flags must not hide a COPY source from the coverage check.

    Args:
        tmp_path: Temporary directory holding the fixture Dockerfile.
    """
    dockerfile = tmp_path / "tools.Dockerfile"
    dockerfile.write_text(
        "FROM debian\n"
        "COPY lintro/ /app/lintro/\n"
        "COPY --chmod=0755 scripts/ /app/scripts/\n"
        "COPY --from=builder /out/bin /usr/local/bin\n"
        "COPY package.json pyproject.toml /app/\n"
        f"RUN python3 {_GENERATE_SCRIPT}\n"
        "COPY after/ /app/after/\n",
        encoding="utf-8",
    )

    assert_that(_render_step_copy_sources(dockerfile)).is_equal_to(
        ["lintro/", "scripts/", "package.json", "pyproject.toml"],
    )
