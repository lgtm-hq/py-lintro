# Docker Usage Guide

This guide explains how to use Lintro with Docker for containerized development and CI
environments.

## Quick Start

### Using Published Image (Recommended)

The easiest way to use Lintro is with the pre-built image from GitHub Container
Registry:

```bash
# Basic usage (tools image - includes all external tools)
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check

# With grid formatting
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check --output-format grid

# Run specific tools
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check --tools ruff,prettier

# Format code
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest format

# Base image (minimal, no external tools)
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:base check
```

### Development Setup

```bash
# Clone the repository
git clone https://github.com/lgtm-hq/py-lintro.git
cd py-lintro

# Make the Docker script executable
chmod +x scripts/**/*.sh

# Run Lintro with Docker
./scripts/docker/docker-lintro.sh check --output-format grid
```

### Basic Commands

```bash
# Check code for issues
./scripts/docker/docker-lintro.sh check

# Auto-fix issues where possible
./scripts/docker/docker-lintro.sh format

# Use grid formatting (recommended)
./scripts/docker/docker-lintro.sh check --output-format grid --group-by code

# List available tools
./scripts/docker/docker-lintro.sh list-tools
```

## Published Docker Image

Lintro provides a pre-built Docker image available on GitHub Container Registry (GHCR):

### Image Details

- **Registry**: `ghcr.io/lgtm-hq/py-lintro`
- **Tags**:
  - `latest` - Tools image (recommended; includes external tools)
  - `main` - Main branch version
  - `base` - Minimal image (no external tools)
  - `v1.0.0` - Specific release versions (when available)
  - `v1.0.0-base` - Base image for that release

### Using the Published Image

```bash
# Pull the latest image
docker pull ghcr.io/lgtm-hq/py-lintro:latest

# Run with the published image (tools image)
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check

# Use a specific version
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:main check

# Use the base image (minimal)
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:base check
```

### CI/CD Integration

Use the published image in your CI/CD pipelines:

```yaml
# GitHub Actions example
- name: Run Lintro
  run: |
    docker run --rm -v ${{ github.workspace }}:/code \
      ghcr.io/lgtm-hq/py-lintro:latest check --output-format grid

# GitLab CI example
lintro:
  image: ghcr.io/lgtm-hq/py-lintro:latest
  script:
    - lintro check --output-format grid

# Base image example (minimal, no external tools)
lintro_base:
  image: ghcr.io/lgtm-hq/py-lintro:base
  script:
    - lintro check --output-format grid
```

## Container Auto-Detection

Lintro automatically detects when it is running inside a container (Docker, Podman, LXC,
Kubernetes, etc.) and adjusts its behavior accordingly:

- **Auto-install defaults to enabled** in container environments, so Node.js
  dependencies are installed automatically without requiring `--auto-install` or
  configuration changes.
- **Non-interactive mode** is assumed — confirmation prompts are skipped automatically.
- A **pre-execution summary** is displayed before tools run, showing the detected
  environment ("Container"), which tools will run, and any tools that were skipped (with
  reasons).

Detection checks (in order):

1. `/.dockerenv` file exists (Docker)
2. `/run/.containerenv` file exists (Podman)
3. `CONTAINER` environment variable is set
4. `/proc/1/cgroup` contains `docker`, `lxc`, `containerd`, or `kubepods`

This means **no extra configuration is needed** when running the Docker image — it works
out of the box.

## Node.js Dependency Auto-Install

When using Docker, Lintro automatically installs Node.js dependencies because container
environments enable auto-install by default. This happens when:

1. A `package.json` file exists in the mounted `/code` directory
2. The `node_modules` directory is missing or empty

**How it works:**

- Lintro detects the container environment and enables auto-install
- If `node_modules` is missing, it runs `bun install --frozen-lockfile` (falls back to
  regular `bun install` if lockfile fails)
- If `bun` is unavailable, it uses `npm ci` (falls back to `npm install`)
- This ensures tools like `tsc`, `oxlint`, `prettier`, and `markdownlint-cli2` work
  immediately

**Example:**

```bash
# Mount a TypeScript project - dependencies are auto-installed in Docker
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check --tools tsc
```

**Controlling auto-install:**

You can explicitly control auto-install behavior using environment variables:

```bash
# Disable auto-install even in Docker (override container detection)
docker run --rm -e LINTRO_AUTO_INSTALL_DEPS=0 -v $(pwd):/code \
  ghcr.io/lgtm-hq/py-lintro:latest check --tools tsc

# Or use the --yes flag to skip confirmation prompts for auto-install
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check --tools tsc --yes
```

**Per-tool auto-install** can also be configured in `.lintro-config.yaml`:

```yaml
tools:
  tsc:
    auto_install: true # Enable auto-install for tsc specifically
  prettier:
    auto_install: false # Disable auto-install for prettier
```

**Note:** For local usage (outside Docker), use the `--auto-install` flag, set
`auto_install_deps: true` in your configuration file, or use per-tool `auto_install`
settings. See [Configuration](configuration.md) for details.

## Building the Image Locally

```bash
# Build the Docker image
docker build -t lintro:latest .

# Or use docker compose
docker compose build
```

## Running Commands

### Using the Shell Script (Recommended)

The `scripts/docker/docker-lintro.sh` script provides the easiest way to run Lintro in
Docker:

```bash
# Basic usage
./scripts/docker/docker-lintro.sh check --output-format grid

# Specific tools
./scripts/docker/docker-lintro.sh check --tools ruff,prettier

# Format code
./scripts/docker/docker-lintro.sh fmt --tools ruff

# Export results
./scripts/docker/docker-lintro.sh check --output-format grid --output results.txt
```

### Using Docker Directly

```bash
# Basic check
docker run --rm -v "$(pwd):/code" lintro:latest check

# With grid formatting
docker run --rm -v "$(pwd):/code" lintro:latest check --output-format grid

# Format code
docker run --rm -v "$(pwd):/code" lintro:latest fmt --tools ruff
```

### Using Docker Compose

```bash
# Check code
docker compose run --rm lintro check

# Format code
docker compose run --rm lintro format --tools ruff

# Specific tools
docker compose run --rm lintro check --tools ruff,prettier
```

## Command Options

### Check Command

```bash
./scripts/docker/docker-lintro.sh check [OPTIONS] [PATHS]...
```

**Options:**

- `--tools TEXT` - Comma-separated list of tools (default: all)
- `--output-format grid` - Format output as a grid table
- `--group-by [file|code|none|auto]` - How to group issues
- `--output FILE` - Save output to file
- `--exclude TEXT` - Patterns to exclude
- `--include-venv` - Include virtual environment directories

**Tool-specific options:**

- `--tool-options TEXT` - Tool-specific options in format tool:option=value

### Format Command

```bash
./scripts/docker/docker-lintro.sh fmt [OPTIONS] [PATHS]...
```

Same options as check command, but only runs tools that can auto-fix issues.

### List Tools Command

```bash
./scripts/docker/docker-lintro.sh list-tools [OPTIONS]
```

**Options:**

- `--show-conflicts` - Show potential conflicts between tools
- `--output FILE` - Save tool list to file

## Output to Files

When using the `--output` option, files are created in your current directory:

```bash
# Save check results
./scripts/docker/docker-lintro.sh check --output-format grid --output results.txt

# Save to subdirectory (make sure it exists)
./scripts/docker/docker-lintro.sh check --output-format grid --output reports/results.txt

# Save tool list
./scripts/docker/docker-lintro.sh list-tools --output tools.txt
```

## Common Use Cases

### Code Quality Checks

```bash
# Basic quality check
./scripts/docker/docker-lintro.sh check --output-format grid

# Group by error type for easier fixing
./scripts/docker/docker-lintro.sh check --output-format grid --group-by code

# Check specific files or directories
./scripts/docker/docker-lintro.sh check src/ tests/ --output-format grid

# Use only specific tools
./scripts/docker/docker-lintro.sh check --tools ruff,pydoclint --output-format grid
```

### Code Formatting

```bash
# Format with all available tools
./scripts/docker/docker-lintro.sh fmt

# Format with specific tools
./scripts/docker/docker-lintro.sh fmt --tools ruff,prettier

# Format specific directories
./scripts/docker/docker-lintro.sh fmt src/ --tools ruff
```

### Docker CI/CD Integration

```bash
# CI-friendly output (no grid formatting)
./scripts/docker/docker-lintro.sh check --output ci-results.txt

# Exit with error code if issues found
./scripts/docker/docker-lintro.sh check && echo "No issues found" || echo "Issues detected"
```

## Testing

### Run Tests in Docker

```bash
# Run all integration tests (including Docker-only tests)
./docker-test.sh

# Run local tests only
./run-tests.sh
```

### Development Workflow

```bash
# Check your changes
./scripts/docker/docker-lintro.sh check --output-format grid

# Fix auto-fixable issues
./scripts/docker/docker-lintro.sh fmt

# Run tests
./docker-test.sh

# Check again to ensure everything is clean
./scripts/docker/docker-lintro.sh check --output-format grid
```

## Volume Permissions

Lintro's Docker image automatically handles volume permission mismatches. When the
container starts as root (the default), the entrypoint detects the UID/GID that owns the
mounted `/code` directory and re-executes as that user via
[gosu](https://github.com/tianon/gosu). This means:

- `docker run --rm -v "$(pwd):/code" lintro check` **just works** — no `--user` flag
  needed
- The container process runs as the same UID that owns your project files
- `bun install` (auto-install) can write `node_modules` into the project directory
- No files on the host have their ownership changed

### How it works

1. Container starts as root (entrypoint runs as PID 1)
2. Entrypoint reads the volume owner's UID and GID separately (`stat -c '%u' /code` and
   `stat -c '%g' /code`)
3. If the detected UID/GID differs from the current user, the entrypoint re-execs itself
   as that UID:GID via `gosu`
4. HOME, CARGO_HOME, and BUN_INSTALL are redirected to `/tmp` (the mapped UID won't have
   a home directory inside the container)
5. Lintro runs as the matched UID:GID — full read/write access to `/code`

### Restricted environments

In some environments, the UID auto-detection cannot be used:

| Environment                          | Why                                               | Workaround                                                                 |
| ------------------------------------ | ------------------------------------------------- | -------------------------------------------------------------------------- |
| Kubernetes with `runAsNonRoot: true` | Pod is rejected before entrypoint runs            | Set `securityContext.runAsUser` to match the volume owner and use `--user` |
| Read-only mounts (`-v ...:/code:ro`) | Correct UID but writes still fail                 | Pre-install `node_modules` or remove `:ro`                                 |
| Rootless Docker / Podman             | Usually works, but UID namespace remapping varies | Use `--user "$(id -u):$(id -g)"` if auto-detection fails                   |

When using `--user` explicitly, Lintro detects the non-root context and automatically
redirects HOME, CARGO_HOME, and BUN_INSTALL to writable locations (`/tmp`).

## Troubleshooting

### Permission Issues

Volume permissions are handled automatically (see
[Volume Permissions](#volume-permissions) above). If you still encounter permission
errors:

```bash
# Fallback: explicitly pass your user ID
docker run --rm -v "$(pwd):/code" --user "$(id -u):$(id -g)" lintro:latest check
```

This is typically only needed in restricted environments like Kubernetes with
`runAsNonRoot` pod security policies.

### Volume Mounting Issues

Ensure you're using the absolute path for volume mounting:

```bash
# Use absolute path
docker run --rm -v "$(pwd):/code" lintro:latest check

# Check current directory
pwd
```

### Docker Script Issues

If the `scripts/docker/docker-lintro.sh` script isn't working:

1. **Check permissions:** `chmod +x scripts/docker/docker-lintro.sh`
2. **Verify Docker is running:** `docker --version`
3. **Ensure you're in the correct directory:** Should contain `Dockerfile`

### Build Issues

If Docker build fails:

```bash
# Clean build (no cache)
docker build --no-cache -t lintro:latest .

# Check Docker logs
docker build -t lintro:latest . 2>&1 | tee build.log
```

## Advanced Usage

### Custom Configuration

Build a custom image with your own configuration:

```bash
# Copy your config files to the container
docker build -t lintro:custom .

# Run with custom config
docker run --rm -v "$(pwd):/code" lintro:custom check
```

### Performance Optimization

For large codebases:

```bash
# Use specific tools only
./scripts/docker/docker-lintro.sh check --tools ruff --output-format grid

# Exclude unnecessary patterns
./scripts/docker/docker-lintro.sh check --exclude "*.pyc,venv,node_modules" --output-format grid

# Process specific directories
./scripts/docker/docker-lintro.sh check src/ --output-format grid
```

## Integration with Other Tools

### Makefile Integration

<!-- markdownlint-disable MD010 -- Makefile code block requires literal hard tabs -->

```makefile
lint:
	./scripts/docker/docker-lintro.sh check --output-format grid

fix:
	./scripts/docker/docker-lintro.sh fmt

lint-ci:
	./scripts/docker/docker-lintro.sh check --output lint-results.txt
```

<!-- markdownlint-enable MD010 -->

### GitHub Actions

```yaml
- name: Run Lintro
  run: |
    chmod +x scripts/docker/docker-lintro.sh
    ./scripts/docker/docker-lintro.sh check --output-format grid --output lintro-results.txt
```

## Skipped Tools and Pre-Execution Summary

When running in Docker, Lintro displays a **pre-execution configuration summary** before
tools run. This table shows:

- **Environment**: Container (auto-detected) or Local
- **Auto-install**: Whether auto-install is enabled and why
- **Tools**: Which tools will run
- **Skipped tools**: Tools that were skipped with reasons

Tools can be skipped for several reasons:

| Reason                   | Description                                              |
| ------------------------ | -------------------------------------------------------- |
| `node_modules not found` | Node.js deps missing and auto-install is disabled        |
| `disabled in config`     | Tool explicitly disabled via `tools.<name>.enabled`      |
| `not in enabled_tools`   | Tool not listed in `execution.enabled_tools`             |
| `deferred to <tool>`     | Framework-specific tool preferred (e.g., tsc to vue-tsc) |
| `version check failed`   | Tool version below minimum required                      |

Skipped tools appear in the summary table with a `SKIP` status and a note explaining
why, so you always know what happened.

**Example output:**

```text
┌─────────────┬────────┬────────┬─────────────────────────┐
│ Tool        │ Status │ Issues │ Notes                   │
├─────────────┼────────┼────────┼─────────────────────────┤
│ ruff        │ PASS   │ 0      │                         │
│ tsc         │ SKIP   │ -      │ deferred to astro-check │
│ astro-check │ PASS   │ 0      │                         │
└─────────────┴────────┴────────┴─────────────────────────┘
```

## Best Practices

1. **Use grid formatting** for better readability: `--output-format grid`
2. **Group by error type** for systematic fixing: `--group-by code`
3. **Save results to files** for CI integration: `--output results.txt`
4. **Use specific tools** for faster checks: `--tools ruff,prettier`
5. **Exclude irrelevant files** to reduce noise: `--exclude "venv,node_modules"`
6. **Use `--yes`** to skip confirmation prompts in scripts: `lintro check --yes`
