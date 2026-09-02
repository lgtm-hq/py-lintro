#!/usr/bin/env python3
"""Copy repo-root docs/ into apps/site/src/content/docs/ with Astro frontmatter.

Every markdown file under ``docs/`` is mapped into one of six task-based site
sections (start, guides, ai, tools, contribute, project). The mapping is a
single table (:data:`DOC_SPECS` plus the tool-analysis rule) and the migration
refuses to run when a source file is not covered, so a new doc cannot silently
drop off the published site.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCS_SRC = ROOT / "docs"
DOCS_DEST = ROOT / "apps" / "site" / "src" / "content" / "docs"
ROUTE_MAP_DEST = ROOT / "apps" / "site" / "src" / "generated" / "docs-route-map.ts"

SECTION_LABELS: dict[str, str] = {
    "start": "Start",
    "guides": "Guides",
    "ai": "AI",
    "tools": "Tools",
    "contribute": "Contribute",
    "project": "Project",
}


@dataclass(frozen=True)
class DocSpec:
    """Where one source markdown file lands on the site.

    Attributes:
        source: Path relative to ``docs/`` as authored in cross-links.
        category: Site section key (one of :data:`SECTION_LABELS`).
        slug: Destination id inside the section; ``index`` marks the section
            landing page and nested ``adr/index`` style ids are allowed.
        order: Sort position inside the section.
        nav_title: Short label used in the sidebar and page header.
        nav_group: Sidebar group key inside the section.
    """

    source: str
    category: str
    slug: str
    order: int
    nav_title: str
    nav_group: str | None = None


# Source files that intentionally never reach the site.
SKIP_SOURCES: frozenset[str] = frozenset({"adr/template.md"})

DOC_SPECS: tuple[DocSpec, ...] = (
    # Start
    DocSpec("README.md", "start", "overview", 5, "Overview"),
    DocSpec(
        "getting-started.md",
        "start",
        "getting-started",
        10,
        "Getting started",
        "start",
    ),
    DocSpec("comparison.md", "start", "comparison", 20, "Comparison", "evaluate"),
    # Guides
    DocSpec("usage/README.md", "guides", "index", 5, "Guides"),
    DocSpec(
        "configuration.md",
        "guides",
        "configuration",
        10,
        "Configuration",
        "setup",
    ),
    DocSpec("watch-mode.md", "guides", "watch-mode", 20, "Watch mode", "setup"),
    DocSpec("docker.md", "guides", "docker", 30, "Docker", "setup"),
    DocSpec("pre-commit.md", "guides", "pre-commit", 40, "Pre-commit", "ci"),
    DocSpec(
        "github-integration.md",
        "guides",
        "github-integration",
        50,
        "GitHub Actions",
        "ci",
    ),
    DocSpec(
        "npm-distribution.md",
        "guides",
        "npm-distribution",
        60,
        "npm distribution",
        "distribute",
    ),
    DocSpec(
        "usage/library-api.md",
        "guides",
        "library-api",
        70,
        "Library API",
        "distribute",
    ),
    DocSpec(
        "troubleshooting.md",
        "guides",
        "troubleshooting",
        80,
        "Troubleshooting",
        "debug",
    ),
    DocSpec("debugging.md", "guides", "debugging", 90, "Debugging", "debug"),
    # AI
    DocSpec("ai-features.md", "ai", "ai-features", 10, "AI features", "features"),
    DocSpec(
        "ai-review-transports.md",
        "ai",
        "review-transports",
        20,
        "Review transports",
        "review",
    ),
    DocSpec(
        "ai-review-report.md",
        "ai",
        "review-report",
        30,
        "Reading a review report",
        "review",
    ),
    DocSpec("mcp.md", "ai", "mcp", 40, "MCP server", "agents"),
    DocSpec(
        "architecture/AI-REVIEW-EXECUTION.md",
        "ai",
        "review-execution",
        50,
        "Review execution",
        "internals",
    ),
    # Contribute
    DocSpec("contributing.md", "contribute", "index", 5, "Contributing"),
    DocSpec(
        "contributing/adding-a-new-tool.md",
        "contribute",
        "adding-a-new-tool",
        10,
        "Adding a tool",
        "develop",
    ),
    DocSpec("testing.md", "contribute", "testing", 20, "Testing", "develop"),
    DocSpec("plugins.md", "contribute", "plugins", 30, "Plugins", "develop"),
    DocSpec(
        "style-guide.md",
        "contribute",
        "style-guide",
        40,
        "Style guide",
        "standards",
    ),
    DocSpec(
        "SHELL-SCRIPT-STYLE-GUIDE.md",
        "contribute",
        "shell-script-style-guide",
        50,
        "Shell script style",
        "standards",
    ),
    DocSpec(
        "lintro-self-use.md",
        "contribute",
        "self-use",
        60,
        "Self-use",
        "practices",
    ),
    # Project
    DocSpec("architecture/README.md", "project", "index", 5, "Project"),
    DocSpec(
        "architecture/ARCHITECTURE.md",
        "project",
        "architecture",
        10,
        "Architecture",
        "architecture",
    ),
    DocSpec(
        "architecture/VISION.md",
        "project",
        "vision",
        20,
        "Vision",
        "architecture",
    ),
    DocSpec(
        "architecture/ROADMAP.md",
        "project",
        "roadmap",
        30,
        "Roadmap",
        "architecture",
    ),
    DocSpec(
        "adr/README.md",
        "project",
        "adr/index",
        40,
        "Decision records",
        "decisions",
    ),
    DocSpec(
        "design/README.md",
        "project",
        "design/index",
        60,
        "Design notes",
        "design",
    ),
    DocSpec(
        "security/README.md",
        "project",
        "security/index",
        80,
        "Security",
        "security",
    ),
    DocSpec(
        "security/assurance.md",
        "project",
        "security/assurance",
        81,
        "Assurance",
        "security",
    ),
    DocSpec(
        "security/requirements.md",
        "project",
        "security/requirements",
        82,
        "Requirements",
        "security",
    ),
)

# Directories whose files are mapped by rule rather than one row each.
ADR_DIR = "adr"
DESIGN_DIR = "design"
TOOLS_DIR = "tool-analysis"

TOOL_GROUPS: dict[str, str] = {
    "actionlint": "ci-ops",
    "astro-check": "frameworks",
    "bandit": "python",
    "black": "python",
    "buf": "config",
    "cargo-deny": "rust",
    "clippy": "rust",
    "commitlint": "ci-ops",
    "dotenv-linter": "config",
    "golangci-lint": "go",
    "hadolint": "ci-ops",
    "html-validate": "frameworks",
    "idiom-review": "python",
    "markdownlint": "docs",
    "mypy": "python",
    "osv-scanner": "security",
    "oxc": "js-ts",
    "pip-audit": "security",
    "prettier": "js-ts",
    "pydoclint": "python",
    "pytest": "python",
    "ruff": "python",
    "spectral": "config",
    "stylelint": "js-ts",
    "svelte-check": "frameworks",
    "trufflehog": "security",
    "tsc": "js-ts",
    "typos": "docs",
    "vale": "docs",
    "vue-tsc": "frameworks",
    "yamllint": "config",
}

# Sections without a README of their own get a generated landing page.
GENERATED_LANDINGS: dict[str, tuple[str, str]] = {
    "ai": (
        "AI",
        "Optional, bring-your-own-key features: summaries and interactive fixes on "
        "every check, a diff-based review that posts to pull requests, and an MCP "
        "server so coding agents can call the same tools. Everything here is off "
        "by default and never moves the deterministic health-score gate.",
    ),
}


ROOT_README_URL = "https://github.com/lgtm-hq/py-lintro"
REPO_LINK = re.compile(
    r"\((?P<ups>(?:\.\./)+)(?P<path>[^)#\s]*)(?P<hash>#[A-Za-z0-9._-]*)?\)",
)


def rewrite_root_readme_links(body: str, src_dir: str) -> str:
    """Point links that escape ``docs/`` at the file on GitHub.

    Only links whose ``../`` chain leaves ``docs/`` are rewritten — e.g.
    ``../README.md`` from ``docs/README.md`` targets the repo root, while the
    same link from ``docs/architecture/`` targets the docs hub and is left
    alone. The repo-root README maps to the repository home page (GitHub
    renders it there, anchors included); any other file maps to its ``blob``
    URL and a directory to its ``tree`` URL, since neither has a page on the
    site.

    Args:
        body: Markdown source being migrated.
        src_dir: Source directory relative to ``docs/`` ("" for the root).

    Returns:
        The body with repo-relative links rewritten.
    """
    depth = len([part for part in src_dir.split("/") if part])

    def _replace(match: re.Match[str]) -> str:
        ups = match.group("ups").count("../")
        if ups != depth + 1:
            return match.group(0)
        path = match.group("path")
        hash_part = match.group("hash") or ""
        if path in {"", "README.md"}:
            return f"({ROOT_README_URL}{hash_part})"
        kind = "tree" if path.endswith("/") else "blob"
        return f"({ROOT_README_URL}/{kind}/main/{path.rstrip('/')}{hash_part})"

    return REPO_LINK.sub(_replace, body)


def title_from_markdown(text: str, fallback: str) -> str:
    """Extract the first markdown H1 title or return ``fallback``.

    Args:
        text: Markdown source.
        fallback: Title used when the source has no H1.

    Returns:
        The page title.
    """
    for line in text.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return fallback


def description_from_markdown(text: str) -> str:
    """Build a short description from prose after the first H1.

    Args:
        text: Markdown source.

    Returns:
        Up to 200 characters of the opening prose, or an empty string.
    """
    lines = text.splitlines()
    started = False
    in_fence = False
    parts: list[str] = []
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("# "):
            started = True
            continue
        if not started:
            continue
        if line.startswith("#"):
            break
        stripped = line.strip()
        if not stripped or stripped[0] in "<>|!-*":
            continue
        parts.append(stripped)
        if len(" ".join(parts)) > 160:
            break
    desc = " ".join(parts)
    return desc[:200] if desc else ""


def slug_name(path: Path) -> str:
    """Normalize a source path stem into a URL-friendly slug.

    Args:
        path: Source markdown path.

    Returns:
        Lowercase, hyphenated slug.
    """
    return path.stem.lower().replace("_", "-")


def strip_leading_h1(text: str) -> str:
    """Drop the first H1 line; the layout renders the title itself.

    Args:
        text: Markdown source.

    Returns:
        The source without its first ``# `` heading line.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("# "):
            del lines[index]
            break
        if line.strip() and not line.startswith("<!--"):
            break
    return "".join(lines)


def adr_nav_title(body: str, slug: str) -> str:
    """Short sidebar label for an ADR, e.g. ``ADR-0001 Native parser per tool``.

    Args:
        body: ADR markdown source.
        slug: ADR file slug used as a fallback.

    Returns:
        The sidebar label.
    """
    title = title_from_markdown(body, slug)
    match = re.match(r"ADR-(\d+):\s*(.+)", title)
    if match:
        return f"ADR-{match.group(1)} {match.group(2).strip()}"
    return title


def rule_specs(docs_src: Path) -> list[DocSpec]:
    """Build specs for directories mapped by rule (ADRs, design notes, tools).

    Args:
        docs_src: The ``docs/`` root.

    Returns:
        Specs for every rule-mapped file that exists.

    Raises:
        ValueError: If a tool page has no entry in :data:`TOOL_GROUPS`.
    """
    specs: list[DocSpec] = []

    adr_dir = docs_src / ADR_DIR
    if adr_dir.is_dir():
        order = 41
        for src in sorted(adr_dir.glob("*.md")):
            rel = f"{ADR_DIR}/{src.name}"
            if src.name == "README.md" or rel in SKIP_SOURCES:
                continue
            slug = slug_name(src)
            body = src.read_text(encoding="utf-8")
            specs.append(
                DocSpec(
                    rel,
                    "project",
                    f"adr/{slug}",
                    order,
                    adr_nav_title(body, slug),
                    "decisions",
                ),
            )
            order += 1

    design_dir = docs_src / DESIGN_DIR
    if design_dir.is_dir():
        order = 61
        for src in sorted(design_dir.glob("*.md")):
            rel = f"{DESIGN_DIR}/{src.name}"
            if src.name == "README.md" or rel in SKIP_SOURCES:
                continue
            slug = slug_name(src)
            body = src.read_text(encoding="utf-8")
            nav_title = title_from_markdown(body, slug).split(" — ")[0].strip()
            specs.append(
                DocSpec(rel, "project", f"design/{slug}", order, nav_title, "design"),
            )
            order += 1

    tools_dir = docs_src / TOOLS_DIR
    if tools_dir.is_dir():
        order = 20
        for src in sorted(tools_dir.glob("*.md")):
            rel = f"{TOOLS_DIR}/{src.name}"
            if rel in SKIP_SOURCES:
                continue
            if src.name == "README.md":
                specs.append(DocSpec(rel, "tools", "index", 5, "Tools"))
                continue
            slug = re.sub(r"-analysis$", "", slug_name(src))
            group = TOOL_GROUPS.get(slug)
            if group is None:
                msg = f"tool page {rel} has no entry in TOOL_GROUPS"
                raise ValueError(msg)
            specs.append(DocSpec(rel, "tools", slug, order, slug, group))
            order += 10

    return specs


def all_specs(docs_src: Path) -> list[DocSpec]:
    """Every spec that applies to the given docs tree.

    Args:
        docs_src: The ``docs/`` root.

    Returns:
        Table rows whose source exists plus rule-generated rows.
    """
    table = [spec for spec in DOC_SPECS if (docs_src / spec.source).exists()]
    return table + rule_specs(docs_src)


def assert_all_sources_mapped(docs_src: Path, specs: list[DocSpec]) -> None:
    """Fail loudly when a ``docs/`` markdown file has no site mapping.

    Args:
        docs_src: The ``docs/`` root.
        specs: The specs about to be migrated.

    Raises:
        ValueError: If any markdown source is neither mapped nor skipped, or a
            spec names a section that has no label.
    """
    unknown = sorted({spec.category for spec in specs} - set(SECTION_LABELS))
    if unknown:
        msg = (
            f"DOC_SPECS use sections missing from SECTION_LABELS: {', '.join(unknown)}"
        )
        raise ValueError(msg)
    mapped = {spec.source for spec in specs}
    sources = {
        path.relative_to(docs_src).as_posix()
        for path in docs_src.rglob("*.md")
        if path.is_file()
    }
    missing = sorted(sources - mapped - SKIP_SOURCES)
    if missing:
        listed = "\n  ".join(missing)
        msg = (
            "docs/ files without a site mapping (add them to DOC_SPECS or "
            f"SKIP_SOURCES in {Path(__file__).name}):\n  {listed}"
        )
        raise ValueError(msg)


def write_doc(
    dest_rel: str,
    category: str,
    order: int,
    body: str,
    title: str,
    *,
    nav_title: str | None = None,
    nav_group: str | None = None,
    description: str | None = None,
) -> None:
    """Write a docs content file with Astro frontmatter.

    Args:
        dest_rel: Destination path relative to the content root.
        category: Site section key.
        order: Sort position inside the section.
        body: Markdown body.
        title: Page title.
        nav_title: Short sidebar label, written when it differs from ``title``.
        nav_group: Sidebar group key.
        description: Meta description; derived from the body when omitted.
    """
    dest = DOCS_DEST / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if description is None:
        description = description_from_markdown(body)

    def safe(s: str) -> str:
        return s.replace(chr(34), chr(39))

    frontmatter = (
        "---\n"
        f'title: "{safe(title)}"\n'
        f'description: "{safe(description)}"\n'
        f"category: {category}\n"
        f"order: {order}\n"
    )
    if nav_title and nav_title != title:
        frontmatter += f'navTitle: "{safe(nav_title)}"\n'
    if nav_group:
        frontmatter += f"navGroup: {nav_group}\n"
    frontmatter += "---\n\n"
    dest.write_text(frontmatter + body.lstrip(), encoding="utf-8")


def migrate_spec(spec: DocSpec, docs_src: Path) -> tuple[str, str]:
    """Migrate one source file according to its spec.

    Args:
        spec: The mapping row.
        docs_src: The ``docs/`` root.

    Returns:
        The ``(source, doc_id)`` pair for the route map.
    """
    src = docs_src / spec.source
    src_dir = spec.source.rsplit("/", 1)[0] if "/" in spec.source else ""
    body = rewrite_root_readme_links(src.read_text(encoding="utf-8"), src_dir)
    title = title_from_markdown(body, spec.nav_title)
    description = description_from_markdown(body)
    write_doc(
        f"{spec.category}/{spec.slug}.md",
        spec.category,
        spec.order,
        strip_leading_h1(body),
        title,
        nav_title=spec.nav_title,
        nav_group=spec.nav_group,
        description=description,
    )
    return spec.source, f"{spec.category}/{spec.slug}"


def write_generated_landings(specs: list[DocSpec]) -> None:
    """Write landing pages for sections that have no README of their own.

    Args:
        specs: Migrated specs, used to skip sections that already have a
            landing page.
    """
    landing_slugs = {"index", "overview"}
    has_landing = {spec.category for spec in specs if spec.slug in landing_slugs}
    for category, (title, intro) in GENERATED_LANDINGS.items():
        if category in has_landing:
            continue
        write_doc(
            f"{category}/index.md",
            category,
            5,
            f"{intro}\n",
            title,
            description=intro,
        )


def write_route_map(route_map: dict[str, str]) -> None:
    """Write the source→doc route map consumed by the site's link layer.

    The map keys are paths relative to ``docs/`` exactly as authored in
    markdown cross-links (e.g. ``architecture/ARCHITECTURE.md``); values are
    the migrated Astro doc ids (e.g. ``project/architecture``). The site uses
    it to rewrite ``.md`` cross-links to final ``/docs/<id>/`` routes.

    Args:
        route_map: Mapping of source-relative markdown paths to doc ids.
    """
    ROUTE_MAP_DEST.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "// AUTO-GENERATED by scripts/ci/site/migrate-docs-content.py — do not edit.",
        "// Maps docs/-relative source paths (as authored in markdown cross-links)",
        "// to migrated Astro doc ids under src/content/docs/.",
        "export const sourceToDoc: Record<string, string> = {",
    ]
    for source, doc_id in sorted(route_map.items()):
        lines.append(f'  "{source}": "{doc_id}",')
    lines.append("};")
    ROUTE_MAP_DEST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Copy ``docs/`` sources into ``apps/site/src/content/docs/``."""
    specs = all_specs(DOCS_SRC)
    assert_all_sources_mapped(DOCS_SRC, specs)

    if DOCS_DEST.exists():
        for child in DOCS_DEST.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    route_map: dict[str, str] = {}
    for spec in specs:
        source, doc_id = migrate_spec(spec, DOCS_SRC)
        route_map[source] = doc_id

    write_generated_landings(specs)
    write_route_map(route_map)
    print(f"Migrated {len(specs)} docs to {DOCS_DEST}")
    print(f"Wrote route map to {ROUTE_MAP_DEST}")


if __name__ == "__main__":
    import argparse

    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
