"""Documentation testing suite for Lintro.

This module tests various aspects of the project documentation to ensure
consistency, accuracy, and completeness.
"""

import re
import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_scripts_have_help() -> None:
    """Test that all executable scripts support --help flag."""
    script_dir = _REPO_ROOT / "scripts"
    failed_scripts = []

    for script_file in script_dir.rglob("*.sh"):
        # Skip utility files that are sourced by other scripts
        if script_file.name in [
            "utils.sh",
            "install.sh",
        ]:
            continue

        try:
            result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
                [str(script_file), "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                failed_scripts.append(
                    f"{script_file}: exit code {result.returncode}",
                )
        except subprocess.TimeoutExpired:
            failed_scripts.append(f"{script_file}: timeout")
        except Exception as e:
            failed_scripts.append(f"{script_file}: {e}")

    if failed_scripts:
        pytest.fail("Scripts without --help support:\n" + "\n".join(failed_scripts))


def test_scripts_readme_coverage() -> None:
    """Test that all scripts are documented in scripts/README.md."""
    scripts_readme = _REPO_ROOT / "scripts" / "README.md"
    if not scripts_readme.exists():
        pytest.skip("scripts/README.md not found")

    with open(scripts_readme, encoding="utf-8") as f:
        content = f.read()

    # Get all script files
    script_files = set()
    for script_file in (_REPO_ROOT / "scripts").rglob("*.sh"):
        # Skip files inside private packages (e.g. ``scripts/ci/_generator/``);
        # those are implementation detail of a documented entry script, not
        # separately invokable scripts.
        if any(part.startswith("_") for part in script_file.parts):
            continue
        script_files.add(script_file.name)
    for script_file in (_REPO_ROOT / "scripts").rglob("*.py"):
        if script_file.name == "__init__.py":
            continue
        # Skip files inside private packages (e.g. ``scripts/ci/_generator/``);
        # those are implementation detail of a documented entry script, not
        # separately invokable scripts.
        if any(part.startswith("_") for part in script_file.parts):
            continue
        script_files.add(script_file.name)

    # Find documented scripts
    documented_scripts = set()
    for script_name in script_files:
        if script_name in content:
            documented_scripts.add(script_name)

    missing_docs = script_files - documented_scripts
    if missing_docs:
        pytest.fail(
            "Scripts not documented in scripts/README.md:\n" + "\n".join(missing_docs),
        )


def test_release_scripts_catalogued_outside_ci_table() -> None:
    """SPDX/Version-PR generators live under Release Scripts, not CI/CD."""
    content = Path("scripts/README.md").read_text(encoding="utf-8")
    ci_heading = "### 🔧 CI/CD Scripts (`ci/`)"
    release_heading = "### 🏷️ Release Scripts (`release/`)"
    docker_heading = "### 🐳 Docker Scripts (`docker/`)"
    assert_that(content).contains(ci_heading)
    assert_that(content).contains(release_heading)

    ci_section = content[content.index(ci_heading) : content.index(release_heading)]
    release_section = content[
        content.index(release_heading) : content.index(docker_heading)
    ]
    assert_that(ci_section).does_not_contain("generate_spdx_data.py")
    assert_that(ci_section).does_not_contain("prepare_version_artifacts.py")
    assert_that(release_section).contains("generate_spdx_data.py")
    assert_that(release_section).contains("prepare_version_artifacts.py")
    assert_that(release_section).contains("SECURITY.md")
    assert_that(release_section).contains("version-update-script")


def test_cli_help_works() -> None:
    """Test that lintro --help works and shows expected commands."""
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["uv", "run", "python", "-m", "lintro", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=_REPO_ROOT,
        )
        assert_that(result.returncode).is_equal_to(0)
        assert_that(result.stdout).contains("check")
        assert_that(result.stdout).contains("format")
        assert_that(result.stdout).contains("list-tools")
    except subprocess.TimeoutExpired:
        pytest.fail("lintro --help timed out")


def _slugify_heading(heading: str) -> str:
    """Slugify heading text the way GitHub does.

    GitHub renders the heading first and then slugs the resulting *text*, so
    inline markup (emphasis, inline code, links) contributes its content but
    not its markers. ``github-slugger`` then lowercases, drops punctuation and
    turns spaces into hyphens. Its character class keeps ``-`` and ``_`` — both
    survive into the anchor — which is why underscores are retained here.

    Args:
        heading: Raw heading text, without the leading ``#`` markers.

    Returns:
        The anchor GitHub would generate for that heading.
    """
    # Render-ish pass: keep the text inside inline markup, drop the markers.
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # links/images
    text = re.sub(r"`([^`]*)`", r"\1", text)  # inline code
    text = re.sub(r"(\*{1,3})(\S.*?\S|\S)\1", r"\2", text)  # *em* / **strong**
    text = re.sub(r"(?<!\w)(_{1,3})(\S.*?\S|\S)\1(?!\w)", r"\2", text)  # _em_
    # github-slugger: lowercase, strip punctuation except "-" and "_", " " -> "-"
    slug = re.sub(r"[^\w\- ]", "", text.lower())
    return slug.replace(" ", "-")


def _heading_anchors(markdown: str) -> set[str]:
    """Collect the anchors a Markdown document exposes.

    Supports both GitHub's implicit heading slugs and the explicit
    ``{#custom-anchor}`` suffix used in the longer guides. Fenced code blocks
    are skipped so that shell comments such as ``# install foo`` do not
    register as headings and silently validate a link that does not resolve.

    Repeated headings get the ``-1``, ``-2`` … suffixes ``github-slugger``
    assigns, so a link to the second occurrence is not reported as broken.

    Args:
        markdown: Full text of a Markdown document.

    Returns:
        Set of anchor names (without the leading ``#``).
    """
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    fence: str | None = None
    for line in markdown.splitlines():
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match is not None:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            continue
        if fence is not None:
            continue

        match = re.match(r"^#{1,6}\s+(.*)$", line)
        if match is None:
            continue
        heading = match.group(1).strip()
        explicit = re.search(r"\{#([^}]+)\}\s*$", heading)
        if explicit is not None:
            anchors.add(explicit.group(1))
            heading = heading[: explicit.start()].strip()

        slug = _slugify_heading(heading)
        occurrence = seen.get(slug, 0)
        seen[slug] = occurrence + 1
        anchors.add(slug if occurrence == 0 else f"{slug}-{occurrence}")
    return anchors


def test_slugify_heading_matches_github() -> None:
    """Heading slugs match anchors GitHub's renderer actually emits.

    Expected values were taken from GitHub's ``POST /markdown`` API, which
    returns ``id="user-content-<anchor>"`` for each rendered heading. Note that
    underscores survive (``github-slugger`` keeps ``_`` and ``-``) while
    emphasis markers do not, because GitHub slugs the rendered text.
    """
    assert_that(_slugify_heading("snake_case option names")).is_equal_to(
        "snake_case-option-names",
    )
    assert_that(_slugify_heading("What Lintro does _not_ do yet")).is_equal_to(
        "what-lintro-does-not-do-yet",
    )
    assert_that(_slugify_heading("Ruff vs. Black Policy (Python)")).is_equal_to(
        "ruff-vs-black-policy-python",
    )
    assert_that(_slugify_heading("Node.js Tool Resolution")).is_equal_to(
        "nodejs-tool-resolution",
    )


def test_heading_anchors_ignore_fenced_code_blocks() -> None:
    """Shell comments inside fences must not register as headings."""
    markdown = "# Real\n\n```bash\n# npm install -g typescript\n```\n\n## Second\n"

    anchors = _heading_anchors(markdown)

    assert_that(anchors).contains("real", "second")
    assert_that(anchors).does_not_contain("npm-install--g-typescript")


def test_heading_anchors_number_repeated_headings() -> None:
    """Repeated headings get GitHub's ``-1``/``-2`` occurrence suffixes."""
    markdown = "## Installation\n\n## Installation\n\n## Installation\n"

    anchors = _heading_anchors(markdown)

    assert_that(anchors).contains("installation", "installation-1", "installation-2")


def test_internal_doc_links() -> None:
    """Test that internal documentation links are valid."""
    doc_files = [
        "README.md",
        "docs/getting-started.md",
        "docs/contributing.md",
        "docs/docker.md",
        "docs/github-integration.md",
        "scripts/README.md",
    ]

    broken_links = []
    for doc_file in doc_files:
        doc_path = _REPO_ROOT / doc_file
        if not doc_path.exists():
            continue

        with open(doc_path, encoding="utf-8") as f:
            content = f.read()

        # Find markdown links
        links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
        for link_text, link_url in links:
            # Skip anything that leaves the repository or is not a file link.
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", link_url) or link_url.startswith(
                "//",
            ):
                continue

            link_path, _, fragment = link_url.partition("#")
            # A bare "#anchor" points at the current document.
            source = doc_path
            target = source if not link_path else (source.parent / link_path).resolve()

            if not target.exists():
                broken_links.append(f"{doc_file}: {link_text} -> {link_url}")
                continue

            if fragment and target.is_file() and target.suffix == ".md":
                anchors = _heading_anchors(target.read_text(encoding="utf-8"))
                if fragment not in anchors:
                    broken_links.append(
                        f"{doc_file}: {link_text} -> {link_url} (missing anchor)",
                    )

    if broken_links:
        pytest.fail("Broken internal links:\n" + "\n".join(broken_links))


def test_all_docs_have_titles() -> None:
    """Test that all documentation files have proper titles."""
    doc_files = [
        "README.md",
        "docs/getting-started.md",
        "docs/contributing.md",
        "docs/docker.md",
        "docs/github-integration.md",
        "docs/configuration.md",
        "scripts/README.md",
    ]

    files_without_titles = []
    for doc_file in doc_files:
        doc_path = _REPO_ROOT / doc_file
        if not doc_path.exists():
            continue

        with open(doc_path, encoding="utf-8") as f:
            first_line = f.readline().strip()

        if not first_line.startswith("# "):
            files_without_titles.append(doc_file)

    if files_without_titles:
        pytest.fail("Docs without titles:\n" + "\n".join(files_without_titles))


def test_command_consistency() -> None:
    """Test that CLI commands are consistently documented."""
    doc_files = [
        "README.md",
        "docs/getting-started.md",
        "docs/configuration.md",
    ]

    inconsistent_commands = []
    for doc_file in doc_files:
        doc_path = _REPO_ROOT / doc_file
        if not doc_path.exists():
            continue

        with open(doc_path, encoding="utf-8") as f:
            content = f.read()

        # `chk`, `fmt`, and `ls` are valid, current CLI aliases (registered in
        # lintro/cli.py), but end-user docs should prefer the canonical command
        # names (`check`, `format`, `list-tools`) for clarity and discoverability.
        alias_to_canonical = {
            "lintro fmt": "lintro format",
            "lintro chk": "lintro check",
            "lintro ls": "lintro list-tools",
        }
        for alias, canonical in alias_to_canonical.items():
            if alias in content:
                inconsistent_commands.append(
                    f"{doc_file}: prefer canonical '{canonical}' over alias '{alias}'",
                )

    if inconsistent_commands:
        pytest.fail(
            "Inconsistent command usage:\n" + "\n".join(inconsistent_commands),
        )


# ---------------------------------------------------------------------------
# Configuration-contract tests (issue #1224)
#
# These pin the documentation to the runtime so the config story stays truthful:
# the tier count, the env vars that are actually read, the loader's parsing of
# documented execution keys, and the SECURITY.md supported-version table.
# ---------------------------------------------------------------------------

# Env vars the docs are allowed to advertise: they MUST be read by the runtime.
_DOCUMENTED_LINTRO_ENV_VARS = {
    "LINTRO_LOG_DIR",
    "LINTRO_VERSION_TIMEOUT",
    "LINTRO_DOCKER",
    "LINTRO_CONFIG",
    "LINTRO_ENABLE_EXTERNAL_PLUGINS",
    "LINTRO_AI_PROVIDER",
    "LINTRO_AI_MODEL",
    "LINTRO_AI_TRANSPORT",
    "LINTRO_AI_ENABLED",
    "LINTRO_AI_REVIEW",
    "LINTRO_AI_MAX_COST_USD",
}

# Env vars that were historically documented but are NOT read by the runtime.
# They must never reappear in user-facing configuration docs.
_PHANTOM_LINTRO_ENV_VARS = {
    "LINTRO_DEFAULT_TIMEOUT",
    "LINTRO_VERBOSE",
    "LINTRO_EXCLUDE",
    "LINTRO_DEFAULT_FORMAT",
    "LINTRO_AUTO_INSTALL_DEPS",
}


def _project_version() -> str:
    """Return the project version from pyproject.toml.

    Returns:
        str: The ``[project].version`` string.
    """
    import tomllib

    with (_REPO_ROOT / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def test_docs_agree_on_tier_count() -> None:
    """README and the Configuration Guide must state the same tier count."""
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    config_doc = (_REPO_ROOT / "docs" / "configuration.md").read_text(
        encoding="utf-8",
    )

    readme_tiers = set(re.findall(r"(\d+)-tier", readme))
    config_tiers = set(re.findall(r"(\d+)-tier", config_doc, flags=re.IGNORECASE))

    assert_that(readme_tiers).described_as("README N-tier phrase").is_not_empty()
    assert_that(config_tiers).described_as("config guide N-tier").is_not_empty()
    assert_that(readme_tiers).is_equal_to(config_tiers)
    # The runtime LintroConfig model documents a 5-tier core model.
    assert_that(config_tiers).contains("5")


def test_documented_env_vars_are_handled() -> None:
    """Every LINTRO_* env var in the config docs must be read by the runtime."""
    config_doc = (_REPO_ROOT / "docs" / "configuration.md").read_text(
        encoding="utf-8",
    )

    # Collect the source text once so we can confirm each var is referenced.
    source_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (_REPO_ROOT / "lintro").rglob("*.py")
    )

    documented = set(re.findall(r"LINTRO_[A-Z_]+", config_doc))
    # Ignore install/plugin-only identifiers that are not user-facing env vars.
    documented -= {"LINTRO_PLUGIN_API_VERSION"}

    for var in documented:
        assert_that(_DOCUMENTED_LINTRO_ENV_VARS).described_as(
            f"{var} documented in configuration.md must be an allowed env var",
        ).contains(var)
        assert_that(source_text).described_as(
            f"{var} must be referenced in lintro/ source",
        ).contains(var)

    # Phantom vars must not have crept back into the docs.
    for var in _PHANTOM_LINTRO_ENV_VARS:
        assert_that(config_doc).described_as(
            f"phantom env var {var} must not be documented",
        ).does_not_contain(var)


def test_config_loader_parses_documented_execution_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documented execution keys (max_workers, artifacts) must be parsed."""
    from lintro.config.config_loader import clear_config_cache, load_config

    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        "execution:\n"
        "  parallel: true\n"
        "  max_workers: 7\n"
        "  artifacts:\n"
        "    - json\n"
        "    - sarif\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    clear_config_cache()
    config = load_config(config_path=config_file)

    assert_that(config.execution.max_workers).is_equal_to(7)
    assert_that(config.execution.artifacts).is_equal_to(["json", "sarif"])
    assert_that(config.execution.parallel).is_true()

    clear_config_cache()


def test_parallel_is_enabled_by_default() -> None:
    """The runtime default for parallel execution must be True (per docs/ROADMAP)."""
    from lintro.config.execution_config import ExecutionConfig

    assert_that(ExecutionConfig().parallel).is_true()


def test_security_md_supports_current_minor() -> None:
    """SECURITY.md must list the current major.minor line as supported."""
    version = _project_version()
    major, minor, *_ = version.split(".")
    current_line = f"{major}.{minor}.x"

    security = (_REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    supported_rows = [
        line for line in security.splitlines() if "|" in line and "✅" in line
    ]

    assert_that(supported_rows).described_as(
        "SECURITY.md must have a supported-version row",
    ).is_not_empty()
    assert_that("\n".join(supported_rows)).described_as(
        f"SECURITY.md must support current line {current_line}",
    ).contains(current_line)


def test_html_validate_docs_pin_matches_manifest() -> None:
    """Configuration docs must stamp the current html-validate pin."""
    from lintro._tool_versions import get_tool_version

    pin = get_tool_version(tool_name="html-validate")
    assert_that(pin).is_not_none()
    config_doc = (_REPO_ROOT / "docs" / "configuration.md").read_text(
        encoding="utf-8",
    )
    assert_that(config_doc).described_as(
        "docs/configuration.md must name the current html-validate pin",
    ).contains(f"currently `{pin}`")


def test_preview_serve_disables_astro_agent_background() -> None:
    """Local preview must stay attached unless a caller opts into background."""
    script = (_REPO_ROOT / "scripts" / "ci" / "site" / "preview-serve.sh").read_text(
        encoding="utf-8",
    )
    assert_that(script).described_as(
        "preview-serve.sh must default ASTRO_PREVIEW_BACKGROUND to 0",
    ).contains('ASTRO_PREVIEW_BACKGROUND="${ASTRO_PREVIEW_BACKGROUND:-0}"')


def test_justfile_contract() -> None:
    """The justfile must declare recipes and command lines without needing just.

    Recipe headers are matched at start-of-line so ``test-unit`` cannot satisfy
    ``test``. ``just --list`` is an extra parse check only when the binary is
    present; CI images without just still lock the file contract.

    """
    justfile = (_REPO_ROOT / "justfile").read_text(encoding="utf-8")
    contributing = (_REPO_ROOT / "docs" / "contributing.md").read_text(
        encoding="utf-8",
    )

    assert_that(justfile).does_not_contain("just.systems/install.sh")
    assert_that(contributing).does_not_contain("just.systems/install.sh")
    assert_that(justfile).contains('"bash", "-euo", "pipefail", "-c"')
    assert_that(justfile).contains("scripts/local/run-tests.sh")
    assert_that(justfile).does_not_contain("scripts/local/local-test.sh")
    assert_that(justfile).contains('replace(TOOLS, " ", ",")')
    assert_that(justfile).contains("just test-unit --")
    assert_that(justfile).contains("|| true")
    assert_that(justfile).contains("docker build --target full")
    assert_that(justfile).contains("./scripts/ci/site/dev.sh")
    assert_that(justfile).contains("./scripts/ci/site/build.sh")
    assert_that(contributing).contains("just test-unit --")
    assert_that(contributing).does_not_contain("just test-unit -v")
    assert_that(contributing).contains("just lintro-check")
    assert_that(contributing).contains("just lintro-format")

    def has_recipe(name: str) -> bool:
        return (
            re.search(rf"^{re.escape(name)}(?:\s|\*|:)", justfile, re.MULTILINE)
            is not None
        )

    for recipe in (
        "setup",
        "install",
        "pre-commit",
        "lint",
        "format",
        "mypy",
        "bench",
        "test",
        "test-integration",
        "test-unit",
        "docker-build",
        "docker-test",
        "clean",
        "site-dev",
        "site-build",
        "site-test",
        "site-preview",
    ):
        assert_that(has_recipe(recipe)).described_as(
            f"justfile must declare a `{recipe}` recipe at start of line",
        ).is_true()

    assert_that(has_recipe("all")).described_as(
        "the all convenience target must stay removed",
    ).is_false()

    just_bin = shutil.which("just")
    if just_bin is None:
        pytest.skip("`just` binary not installed; skipping just --list parse check")

    try:
        result = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [just_bin, "--list"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            cwd=_REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("`just --list` timed out")

    assert_that(result.returncode).is_equal_to(0)
    listed = {
        line.strip().split(" ", 1)[0]
        for line in result.stdout.splitlines()
        if line.strip() and not line.strip().startswith("Available")
    }
    assert_that(listed).contains("pre-commit", "test-integration", "site-dev")
    assert_that(listed).does_not_contain("all")


def test_makefile_is_retired() -> None:
    """The root Makefile is replaced by the justfile and must not return."""
    assert_that((_REPO_ROOT / "Makefile").exists()).described_as(
        "root Makefile was replaced by the justfile",
    ).is_false()


def test_repo_file_paths_are_cwd_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repo file reads must not depend on the process working directory."""
    monkeypatch.chdir(tmp_path)

    assert_that((_REPO_ROOT / "justfile").is_file()).is_true()
    assert_that((_REPO_ROOT / "docs" / "contributing.md").is_file()).is_true()
    assert_that((_REPO_ROOT / "Makefile").exists()).is_false()
