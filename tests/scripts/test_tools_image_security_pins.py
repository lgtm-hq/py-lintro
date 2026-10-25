"""Security floors for copies installed in the external-tools image."""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from lintro.plugins.subprocess_executor import run_subprocess

REPO_ROOT = Path(__file__).resolve().parents[2]

# The python:3.14-slim digest that seeded pip 26.2.1 into every fresh venv,
# with the compromised root still in pip's private CA bundle. Later digests
# are not pinned here: the build-time seed check in each Dockerfile validates
# whatever base Renovate selects, so only this known-bad one is rejected.
PYTHON_BASE_TAG = "python:3.14-slim"
STALE_PYTHON_BASE_DIGEST = (
    "fbecd461fc72d21b2143c6eaba25143738079154a6d0ea3da3aec724f50eed4a"
)
PYTHON_BASE_FROM = re.compile(
    r"^FROM python:3\.14-slim@sha256:([0-9a-f]{64}) AS (\w+)$",
    re.MULTILINE,
)
SEED_CHECK_VENV = "/tmp/lintro-seed-check"
SEED_CHECK_PYTHON = f"{SEED_CHECK_VENV}/bin/python"
SEED_CHECK_PIP_FLOOR = re.compile(
    r'Version\(pip\.__version__\) >= Version\("([0-9.]+)"\)',
)
MIN_SEEDED_PIP = Version("26.2.2")


def _instructions(dockerfile: Path) -> str:
    """Return a Dockerfile's instructions with comments and continuations folded.

    Args:
        dockerfile: Path to the Dockerfile to read.

    Returns:
        str: One line per instruction, comment lines removed and backslash
        continuations joined with a single space.
    """
    text = "\n".join(
        line
        for line in dockerfile.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    return text.replace("\\\n", " ")


def _seed_check_run(instructions: str) -> str:
    """Return the single RUN instruction that self-checks a freshly seeded venv.

    Args:
        instructions: Folded Dockerfile instructions from ``_instructions``.

    Returns:
        str: The complete RUN instruction creating, probing and removing the
        ``/tmp/lintro-seed-check`` venv.
    """
    runs = [
        line
        for line in instructions.splitlines()
        if line.startswith("RUN ") and SEED_CHECK_VENV in line
    ]
    assert len(runs) == 1, "exactly one RUN must own the ensurepip seed check"
    run = runs[0]
    leaked = "the temporary venv must not be referenced outside its own RUN"
    assert instructions.count(SEED_CHECK_VENV) == run.count(SEED_CHECK_VENV), leaked
    return run


def _assert_seed_check_probes_fresh_venv(run: str) -> None:
    """Assert the RUN seeds a venv, probes it from inside, and removes it.

    Args:
        run: The RUN instruction returned by ``_seed_check_run``.
    """
    create = f"python3 -m venv {SEED_CHECK_VENV}"
    probe = f"{SEED_CHECK_PYTHON} -c"
    cleanup = f"rm -rf {SEED_CHECK_VENV}"
    assert create in run, "the check must seed a brand-new venv via ensurepip"
    assert probe in run, "the check must run the venv's own interpreter"
    assert cleanup in run, "the temporary venv must be removed in the same RUN"
    assert run.index(create) < run.index(probe) < run.index(cleanup)
    assert run.count(SEED_CHECK_PYTHON) == 1
    assert "pip._vendor.packaging.version import Version" in run
    lexicographic = "versions must be compared with Version(), not lexicographically"
    assert "__version__ >=" not in run, lexicographic
    assert "__version__ <" not in run, lexicographic
    pip_floor = SEED_CHECK_PIP_FLOOR.search(run)
    assert pip_floor is not None, "the probe must floor the seeded pip itself"
    assert Version(pip_floor.group(1)) >= MIN_SEEDED_PIP


def _python_base_digest(dockerfile: Path, stage: str) -> str:
    """Return the digest a Dockerfile pins for its single python-based stage.

    Args:
        dockerfile: Path to the Dockerfile to inspect.
        stage: Name of the stage expected to build FROM the python base.

    Returns:
        str: The 64-hex sha256 digest pinned on the ``python:3.14-slim`` tag.
    """
    matches = PYTHON_BASE_FROM.findall(dockerfile.read_text())
    assert len(matches) == 1, f"{dockerfile.name} must pin {PYTHON_BASE_TAG} once"
    digest, found_stage = matches[0]
    assert found_stage == stage
    assert digest != STALE_PYTHON_BASE_DIGEST, "stale pip 26.2.1 seed base"
    return digest


def test_python_base_images_share_a_digest_newer_than_the_stale_seed() -> None:
    """Both python-based stages must pin the same digest and drop the stale one."""
    tools = _python_base_digest(
        dockerfile=REPO_ROOT / "docker" / "tools.Dockerfile",
        stage="tools",
    )
    base = _python_base_digest(
        dockerfile=REPO_ROOT / "Dockerfile",
        stage="base",
    )
    assert tools == base, "both stages must build from the same python base"


def test_tools_image_verifies_the_fresh_venv_seed_after_system_pip() -> None:
    """The tools layer must prove ensurepip seeds a safe pip after upgrading pip."""
    instructions = _instructions(REPO_ROOT / "docker" / "tools.Dockerfile")
    run = _seed_check_run(instructions)
    _assert_seed_check_probes_fresh_venv(run)
    system_pip = 'RUN uv pip install --system --no-deps "pip==${PIP_VERSION}"'
    assert instructions.index(system_pip) < instructions.index(run)
    assert instructions.index(run) < instructions.index("COPY lintro/ /app/lintro/")


def test_base_image_verifies_the_fresh_venv_seed_after_base_selection() -> None:
    """The root base stage must self-check its seed before installing anything."""
    instructions = _instructions(REPO_ROOT / "Dockerfile")
    run = _seed_check_run(instructions)
    _assert_seed_check_probes_fresh_venv(run)
    base_from = PYTHON_BASE_FROM.search(instructions)
    assert base_from is not None and base_from.group(2) == "base"
    base_stage = instructions[base_from.end() :]
    next_stage = re.search(r"^FROM ", base_stage, re.MULTILINE)
    assert next_stage is not None
    base_stage = base_stage[: next_stage.start()]
    assert run in base_stage, "the seed check must live in the base stage"
    assert base_stage.index(run) < base_stage.index("RUN apt-get update")
    assert base_stage.index(run) < base_stage.index(
        "COPY --from=tools /usr/local/bin/uv",
    )


def test_tools_image_rebuilds_gitleaks_with_its_pinned_go_compiler() -> None:
    """A new system Go must replace the runtime embedded in the release binary."""
    instructions = _instructions(REPO_ROOT / "docker" / "tools.Dockerfile")
    builds = [
        line
        for line in instructions.splitlines()
        if line.startswith("RUN ")
        and "go -C /app/scripts/utils/gitleaks install" in line
    ]
    assert len(builds) == 1, "rebuild Gitleaks instead of shipping its old embedded Go"
    build = builds[0]
    assert "get_tool_version" in build and "'gitleaks'" in build
    assert "GOTOOLCHAIN=local" in build
    assert "CGO_ENABLED=0" in build
    assert "GOSUMDB=sum.golang.org" in build
    assert "GOPROXY=https://proxy.golang.org" in build
    assert "GOBIN=/usr/local/bin" in build
    assert "install -mod=readonly -trimpath -tags gore2regex" in build
    assert (
        "github.com/zricethezav/gitleaks/v8/version.Version=${GITLEAKS_VERSION}"
        in build
    )
    assert 'grep -Fx "/usr/local/bin/gitleaks: go${GO_VERSION}"' in build
    assert 'test "$(gitleaks version)" = "${GITLEAKS_VERSION}"' in build
    assert instructions.index("python3 scripts/ci/generate-tool-versions.py") < (
        instructions.index(build)
    )
    assert instructions.index(build) < instructions.index(
        "/app/scripts/utils/install-tools.sh --docker",
    )


def test_gitleaks_build_uses_a_locked_security_dependency_closure() -> None:
    """The release module stays pinned while its vulnerable copies are replaced."""
    closure = REPO_ROOT / "scripts" / "utils" / "gitleaks"
    module = (closure / "go.mod").read_text()
    sums = (closure / "go.sum").read_text()
    versions = dict(
        re.findall(r"^\s*(?:require )?(\S+) (v\S+)", module, re.MULTILINE),
    )
    assert versions["github.com/zricethezav/gitleaks/v8"] == "v8.30.1"
    for name, version in {
        "github.com/klauspost/compress": "v1.18.7",
        "github.com/nwaples/rardecode/v2": "v2.2.1",
        "github.com/ulikunitz/xz": "v0.5.15",
        "golang.org/x/crypto": "v0.56.0",
        "golang.org/x/sys": "v0.47.0",
        "golang.org/x/sync": "v0.22.0",
        "golang.org/x/text": "v0.41.0",
    }.items():
        assert versions[name] == version
        assert re.search(rf"^{re.escape(name)} {version} h1:\S+$", sums, re.MULTILINE)
    assert not re.search(r"^\s*(replace|exclude|toolchain)\b", module, re.MULTILINE)
    instructions = _instructions(REPO_ROOT / "docker" / "tools.Dockerfile")
    assert "go -C /app/scripts/utils/gitleaks install -mod=readonly" in instructions
    assert "list -mod=readonly -m -f '{{.Version}}'" in instructions
    assert '"${GITLEAKS_VERSION}"' in instructions
    assert "GOWORK=off" in instructions
    assert "GONOSUMDB='' GONOPROXY='' GOPRIVATE=''" in instructions


def test_tools_build_callers_allow_the_verified_go_module_downloads() -> None:
    """All native tools builds must allow the module proxy and its blob redirect."""
    for name in ("candidate", "publish", "promote"):
        path = REPO_ROOT / ".github" / "workflows" / f"docker-tools-{name}.yml"
        text = path.read_text()
        assert "egress-policy: block" in text
        assert "allowed-endpoints-mode: replace" in text
        assert "        proxy.golang.org:443\n" in text, name
        assert "        sum.golang.org:443\n" in text, name
        assert "        storage.googleapis.com:443\n" in text, name


def test_tools_image_updates_pips_vendored_trust_store() -> None:
    """Updating application certifi must not leave pip's private copy behind."""
    dockerfile = (REPO_ROOT / "docker" / "tools.Dockerfile").read_text()
    instructions = "\n".join(
        line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
    )
    pin = re.search(r"^ARG PIP_VERSION=([0-9.]+)$", instructions, re.MULTILINE)
    assert pin is not None, "The tools layer must pin a patched pip distribution"
    assert Version(pin.group(1)) >= Version("26.2.2")
    command = 'RUN uv pip install --system --no-deps "pip==${PIP_VERSION}"'
    assert command in instructions
    assert instructions.index("rm -rf /tmp/uv*") < instructions.index(command)
    assert instructions.index(command) < instructions.index(
        "/app/scripts/utils/install-tools.sh --docker",
    )


def test_application_lock_updates_pips_vendored_trust_store() -> None:
    """pip-api must not reintroduce an unsafe private bundle in the app venv."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    packages = [package for package in lock["package"] if package["name"] == "pip"]
    assert len(packages) == 1
    assert Version(packages[0]["version"]) >= Version("26.2.2")


def test_tools_image_refreshes_the_inherited_brotli_decoder() -> None:
    """A preinstalled vulnerable libbrotli must still be requested from apt."""
    dockerfile = (REPO_ROOT / "docker" / "tools.Dockerfile").read_text()
    instructions = "\n".join(
        line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
    ).replace("\\\n", " ")
    install = re.search(r"apt-get install ([^&]+)", instructions)
    assert install is not None
    assert "libbrotli1" in install.group(1).split()


@pytest.mark.parametrize(
    ("dockerfile", "stages"),
    [("docker/tools.Dockerfile", ("tools",)), ("Dockerfile", ("full", "base"))],
)
@pytest.mark.parametrize(
    ("installed", "accepted"),
    [
        (None, False),
        ("", False),
        ("1.1.0-2+b7", False),
        ("1.1.0-2+deb13u0", False),
        ("1.1.0-2+deb13u1", True),
        ("1.2.1-1", True),
    ],
)
def test_image_brotli_floor_checks_the_installed_debian_package(
    dockerfile: str,
    stages: tuple[str, ...],
    installed: str | None,
    accepted: bool,
    tmp_path: Path,
) -> None:
    """Every apt-based stage must reject the old decoder, including old tools.

    Args:
        dockerfile: Dockerfile whose apt-based stages are checked.
        stages: Names of stages that must guard their installed decoder.
        installed: Version returned by the fixture, or None for a failed query.
        accepted: Whether the actual dpkg version comparison should succeed.
        tmp_path: Private directory for the dpkg-query executable fixture.
    """
    instructions = _instructions(REPO_ROOT / dockerfile)
    guard = (
        "dpkg --compare-versions \"$(dpkg-query -W -f='${Version}' libbrotli1)\" "
        "ge '1.1.0-2+deb13u1'"
    )
    for stage in stages:
        section = re.search(
            rf"^FROM [^\n]+ AS {stage}\n(.*?)(?=^FROM |\Z)",
            instructions,
            re.MULTILINE | re.DOTALL,
        )
        assert section is not None
        runs = [line for line in section.group(1).splitlines() if guard in line]
        assert len(runs) == 1, "each stage must check its own installed copy"
        run = runs[0]
        install = re.search(r"apt-get install ([^&]+)", run)
        assert install is not None
        assert "libbrotli1" in install.group(1).split()
        assert run.index(install.group()) < run.index(guard)
        assert "&&" in run[install.end() : run.index(guard)]
        assert "&&" in run[run.index(guard) + len(guard) :]
    bash = shutil.which("bash")
    if bash is None or shutil.which("dpkg") is None:
        pytest.skip("Executing the Debian version comparison needs bash and dpkg")
    assert bash is not None
    query = tmp_path / "dpkg-query"
    query.write_text(
        '#!/bin/sh\nprintf "%s" "$BROTLI_TEST_VERSION"\n'
        'exit "$BROTLI_TEST_QUERY_EXIT"\n',
    )
    query.chmod(0o755)
    result = run_subprocess(
        [bash, "-c", guard],
        timeout=10,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "BROTLI_TEST_VERSION": installed or "",
            "BROTLI_TEST_QUERY_EXIT": "1" if installed is None else "0",
        },
    )
    assert (result.returncode == 0) is accepted, result.stderr


def test_tools_black_floor_excludes_the_vulnerable_image_copy() -> None:
    """The image generator reads requirement floors, not resolved uv.lock pins."""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    values = [
        *project["project"]["optional-dependencies"]["full"],
        *project["dependency-groups"]["dev"],
    ]
    requirements = [
        Requirement(value) for value in values if Requirement(value).name == "black"
    ]
    assert len(requirements) == 2
    for requirement in requirements:
        assert "26.10.1" in requirement.specifier
        assert "26.5.1" not in requirement.specifier
        assert any(
            spec.operator in {">=", "==", "~="}
            and Version(spec.version) >= Version("26.10.1")
            for spec in requirement.specifier
        )


def test_tools_sqlfluff_floor_excludes_vulnerable_image_copies() -> None:
    """Install the already locked safe SQLFluff rather than its old floor."""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    requirements = [
        Requirement(value)
        for value in project["project"]["optional-dependencies"]["tools"]
        if Requirement(value).name == "sqlfluff"
    ]
    assert len(requirements) == 1
    assert "4.3.0" in requirements[0].specifier
    assert "4.1.0" not in requirements[0].specifier
    assert any(
        spec.operator in {">=", "==", "~="}
        and Version(spec.version) >= Version("4.3.0")
        for spec in requirements[0].specifier
    )
