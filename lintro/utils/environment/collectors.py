"""Environment information collection functions."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False
import sys
from pathlib import Path

from lintro.utils.environment.ci_environment import CIEnvironment
from lintro.utils.environment.environment_report import EnvironmentReport
from lintro.utils.environment.go_info import GoInfo
from lintro.utils.environment.lintro_info import LintroInfo
from lintro.utils.environment.node_info import NodeInfo
from lintro.utils.environment.project_info import ProjectInfo
from lintro.utils.environment.python_info import PythonInfo
from lintro.utils.environment.ruby_info import RubyInfo
from lintro.utils.environment.rust_info import RustInfo
from lintro.utils.environment.system_info import SystemInfo


def _run_command(command: list[str], *, timeout: int = 5) -> str | None:
    """Run a command and return its output, or None on failure."""
    try:
        result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _extract_version(output: str | None) -> str | None:
    """Extract version number from command output."""
    if not output:
        return None
    # Handle whitespace-only strings
    stripped = output.strip()
    if not stripped:
        return None
    # Common patterns: "X.Y.Z", "vX.Y.Z", "version X.Y.Z"
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", stripped)
    if match:
        return match.group(1)
    # Fallback to first token if no version pattern found
    tokens = stripped.split()
    return tokens[0] if tokens else None


def collect_system_info() -> SystemInfo:
    """Collect operating system and shell information."""
    os_name = platform.system()
    os_version = platform.release()

    # Get friendly platform name
    if os_name == "Darwin":
        mac_ver = platform.mac_ver()[0]
        platform_name = f"macOS {mac_ver}" if mac_ver else "macOS"
    elif os_name == "Linux":
        # Try to get distro info (optional dependency)
        try:
            # Optional Linux-only dependency for distro detection.
            # ImportError is handled gracefully below.
            import distro

            platform_name = f"{distro.name()} {distro.version()}"
        except ImportError:
            platform_name = "Linux"
    elif os_name == "Windows":
        platform_name = f"Windows {platform.win32_ver()[0]}"
    else:
        platform_name = os_name

    return SystemInfo(
        os_name=os_name,
        os_version=os_version,
        platform_name=platform_name,
        architecture=platform.machine(),
        shell=os.environ.get("SHELL"),  # nosec B604 - not a subprocess call
        terminal=os.environ.get("TERM"),
        locale=os.environ.get("LANG") or os.environ.get("LC_ALL"),
    )


def collect_python_info() -> PythonInfo:
    """Collect Python runtime information."""
    # Get pip version
    pip_output = _run_command([sys.executable, "-m", "pip", "--version"])
    pip_version = _extract_version(pip_output)

    # Get uv version
    uv_path = shutil.which("uv")
    uv_version = None
    if uv_path:
        uv_output = _run_command(["uv", "--version"])
        uv_version = _extract_version(uv_output)

    return PythonInfo(
        version=platform.python_version(),
        executable=sys.executable,
        virtual_env=os.environ.get("VIRTUAL_ENV"),
        pip_version=pip_version,
        uv_version=uv_version,
    )


def collect_node_info() -> NodeInfo | None:
    """Collect Node.js runtime information."""
    node_path = shutil.which("node")
    if not node_path:
        return None

    node_output = _run_command(["node", "--version"])
    npm_output = _run_command(["npm", "--version"])
    bun_output = _run_command(["bun", "--version"]) if shutil.which("bun") else None
    pnpm_output = _run_command(["pnpm", "--version"]) if shutil.which("pnpm") else None

    return NodeInfo(
        version=_extract_version(node_output),
        path=node_path,
        npm_version=_extract_version(npm_output),
        bun_version=_extract_version(bun_output),
        pnpm_version=_extract_version(pnpm_output),
    )


def collect_rust_info() -> RustInfo | None:
    """Collect Rust runtime information."""
    rustc_path = shutil.which("rustc")
    if not rustc_path:
        return None

    rustc_output = _run_command(["rustc", "--version"])
    cargo_output = _run_command(["cargo", "--version"])
    rustfmt_output = _run_command(["rustfmt", "--version"])
    clippy_output = _run_command(["cargo", "clippy", "--version"])

    return RustInfo(
        rustc_version=_extract_version(rustc_output),
        cargo_version=_extract_version(cargo_output),
        rustfmt_version=_extract_version(rustfmt_output),
        clippy_version=_extract_version(clippy_output),
    )


def collect_go_info() -> GoInfo | None:
    """Collect Go runtime information."""
    go_path = shutil.which("go")
    if not go_path:
        return None

    version_output = _run_command(["go", "version"])
    gopath_output = _run_command(["go", "env", "GOPATH"])
    goroot_output = _run_command(["go", "env", "GOROOT"])

    return GoInfo(
        version=_extract_version(version_output),
        gopath=gopath_output.strip() if gopath_output else None,
        goroot=goroot_output.strip() if goroot_output else None,
    )


def collect_ruby_info() -> RubyInfo | None:
    """Collect Ruby runtime information."""
    ruby_path = shutil.which("ruby")
    if not ruby_path:
        return None

    ruby_output = _run_command(["ruby", "--version"])
    gem_output = _run_command(["gem", "--version"]) if shutil.which("gem") else None
    bundler_output = (
        _run_command(["bundler", "--version"]) if shutil.which("bundler") else None
    )

    return RubyInfo(
        version=_extract_version(ruby_output),
        gem_version=_extract_version(gem_output),
        bundler_version=_extract_version(bundler_output),
    )


def _find_git_root(start_dir: Path | None = None) -> Path | None:
    """Search upward for .git directory.

    Args:
        start_dir: Directory to start searching from. Defaults to cwd.

    Returns:
        Path to the git root directory, or None if not found.
    """
    current = start_dir or Path.cwd()
    for parent in [current, *list(current.parents)]:
        if (parent / ".git").exists():
            return parent
    return None


def collect_project_info() -> ProjectInfo:
    """Collect project detection information."""
    cwd = Path.cwd()
    git_root = _find_git_root(cwd)

    languages: list[str] = []
    package_managers: dict[str, str] = {}

    # Python
    if (cwd / "pyproject.toml").exists():
        languages.append("Python")
        package_managers["uv/pip"] = "pyproject.toml"
    elif (cwd / "setup.py").exists():
        languages.append("Python")
        package_managers["pip"] = "setup.py"

    # JavaScript/TypeScript
    if (cwd / "package.json").exists():
        languages.append("JavaScript")
        if shutil.which("bun"):
            package_managers["bun"] = "package.json"
        else:
            package_managers["npm"] = "package.json"

        # Detect TypeScript more accurately using short-circuit evaluation
        has_typescript = False
        if (
            (cwd / "tsconfig.json").exists()
            or any(cwd.glob("**/*.ts"))
            or any(cwd.glob("**/*.tsx"))
        ):
            has_typescript = True
        else:
            # Check package.json for typescript dependency
            try:
                import json

                pkg_data = json.loads((cwd / "package.json").read_text())
                deps = pkg_data.get("dependencies", {})
                dev_deps = pkg_data.get("devDependencies", {})
                if "typescript" in deps or "typescript" in dev_deps:
                    has_typescript = True
            except (json.JSONDecodeError, OSError):
                pass

        if has_typescript:
            languages.append("TypeScript")

    # Rust
    if (cwd / "Cargo.toml").exists():
        languages.append("Rust")
        package_managers["cargo"] = "Cargo.toml"

    # Go
    if (cwd / "go.mod").exists():
        languages.append("Go")
        package_managers["go"] = "go.mod"

    # Ruby
    if (cwd / "Gemfile").exists():
        languages.append("Ruby")
        package_managers["bundler"] = "Gemfile"

    return ProjectInfo(
        working_dir=str(cwd),
        git_root=str(git_root) if git_root else None,
        languages=sorted(set(languages)),
        package_managers=package_managers,
    )


def collect_lintro_info() -> LintroInfo:
    """Collect lintro installation information."""
    from lintro import __version__

    # Find config file
    config_file = None
    config_valid = False
    cwd = Path.cwd()

    config_names = [".lintro-config.yaml", ".lintro-config.yml", "lintro.yaml"]
    for name in config_names:
        path = cwd / name
        if path.exists():
            config_file = str(path)
            # Basic validation - check if it's readable YAML
            try:
                import yaml

                with open(path, encoding="utf-8") as f:
                    yaml.safe_load(f)
                config_valid = True
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                config_valid = False
            except yaml.YAMLError:
                config_valid = False
            break

    # Get install path
    import lintro

    install_path = str(Path(lintro.__file__).parent)

    return LintroInfo(
        version=__version__,
        install_path=install_path,
        config_file=config_file,
        config_valid=config_valid,
    )


def detect_ci_environment() -> CIEnvironment | None:
    """Detect CI/CD environment."""
    ci_indicators = {
        "GITHUB_ACTIONS": ("GitHub Actions", {"run_id": "GITHUB_RUN_ID"}),
        "GITLAB_CI": ("GitLab CI", {"job_id": "CI_JOB_ID"}),
        "CIRCLECI": ("CircleCI", {"build_num": "CIRCLE_BUILD_NUM"}),
        "TRAVIS": ("Travis CI", {"build_id": "TRAVIS_BUILD_ID"}),
        "JENKINS_URL": ("Jenkins", {"build_number": "BUILD_NUMBER"}),
        "BUILDKITE": ("Buildkite", {"build_id": "BUILDKITE_BUILD_ID"}),
        "AZURE_PIPELINES": ("Azure Pipelines", {"build_id": "BUILD_BUILDID"}),
        "TEAMCITY_VERSION": ("TeamCity", {"build_number": "BUILD_NUMBER"}),
    }

    for env_var, (name, detail_vars) in ci_indicators.items():
        if os.environ.get(env_var):
            details = {
                key: os.environ.get(var, "")
                for key, var in detail_vars.items()
                if os.environ.get(var)
            }
            return CIEnvironment(name=name, is_ci=True, details=details)

    # Generic CI detection
    if os.environ.get("CI"):
        return CIEnvironment(name="Unknown CI", is_ci=True)

    return None


def collect_environment_vars() -> dict[str, str | None]:
    """Collect relevant environment variables."""
    vars_to_check = [
        "LINTRO_CONFIG",
        "NO_COLOR",
        "FORCE_COLOR",
        "CI",
        "GITHUB_ACTIONS",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "NODE_PATH",
        "PATH",
    ]
    return {var: os.environ.get(var) for var in vars_to_check}


def collect_full_environment() -> EnvironmentReport:
    """Collect complete environment report."""
    return EnvironmentReport(
        lintro=collect_lintro_info(),
        system=collect_system_info(),
        python=collect_python_info(),
        node=collect_node_info(),
        rust=collect_rust_info(),
        ci=detect_ci_environment(),
        env_vars=collect_environment_vars(),
        go=collect_go_info(),
        ruby=collect_ruby_info(),
        project=collect_project_info(),
    )
