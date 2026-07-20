#!/usr/bin/env python3
"""Copy repo-root docs/ into apps/site/src/content/docs/ with Astro frontmatter."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCS_SRC = ROOT / "docs"
DOCS_DEST = ROOT / "apps" / "site" / "src" / "content" / "docs"
ROUTE_MAP_DEST = ROOT / "apps" / "site" / "src" / "generated" / "docs-route-map.ts"

CATEGORY_MAP: dict[str, tuple[str, int]] = {
    "getting-started.md": ("getting-started", 10),
    "configuration.md": ("usage", 20),
    "docker.md": ("usage", 30),
    "github-integration.md": ("usage", 40),
    "ai-features.md": ("usage", 50),
    "troubleshooting.md": ("usage", 60),
    "debugging.md": ("usage", 70),
    "plugins.md": ("usage", 80),
    "contributing.md": ("contributing", 10),
    "style-guide.md": ("contributing", 20),
    "lintro-self-use.md": ("contributing", 30),
    "SHELL-SCRIPT-STYLE-GUIDE.md": ("contributing", 40),
}

ARCHITECTURE_ORDER = {
    "README.md": 5,
    "VISION.md": 10,
    "ARCHITECTURE.md": 20,
    "ROADMAP.md": 30,
}

SECURITY_ORDER = {
    "README.md": 5,
    "assurance.md": 10,
    "requirements.md": 20,
}

USAGE_OVERVIEW_ORDER = {
    "README.md": 5,
}

TOOL_ORDER = {
    "README.md": 5,
}

# Short titles for sidebar and page headers (category/slug -> title, optional navGroup).
DOC_NAV: dict[str, tuple[str, str | None]] = {
    "getting-started/hub": ("hub", None),
    "usage/index": ("usage", None),
    "security/index": ("security", None),
    "getting-started/getting-started": ("getting started", "start"),
    "usage/configuration": ("configuration", "setup"),
    "usage/docker": ("docker", "setup"),
    "usage/github-integration": ("github", "ci"),
    "usage/ai-features": ("ai features", "extend"),
    "usage/troubleshooting": ("troubleshooting", "extend"),
    "usage/debugging": ("debugging", "extend"),
    "usage/plugins": ("plugins", "extend"),
    "contributing/contributing": ("contributing", None),
    "contributing/style-guide": ("style guide", "standards"),
    "contributing/lintro-self-use": ("self-use", "meta"),
    "contributing/shell-script-style-guide": ("shell scripts", "standards"),
    "architecture/overview": ("overview", None),
    "architecture/architecture": ("architecture", "design"),
    "architecture/vision": ("vision", "design"),
    "architecture/roadmap": ("roadmap", "design"),
    "security/assurance": ("assurance", "policy"),
    "security/requirements": ("requirements", "policy"),
    "tools/index": ("tools", None),
    "tools/actionlint": ("actionlint", "ci-ops"),
    "tools/astro-check": ("astro-check", "frameworks"),
    "tools/bandit": ("bandit", "python"),
    "tools/black": ("black", "python"),
    "tools/cargo-deny": ("cargo-deny", "rust"),
    "tools/clippy": ("clippy", "rust"),
    "tools/hadolint": ("hadolint", "ci-ops"),
    "tools/idiom-review": ("idiom-review", "python"),
    "tools/markdownlint": ("markdownlint", "config"),
    "tools/mypy": ("mypy", "python"),
    "tools/osv-scanner": ("osv-scanner", "security"),
    "tools/oxc": ("oxc", "js-ts"),
    "tools/pip-audit": ("pip-audit", "security"),
    "tools/prettier": ("prettier", "js-ts"),
    "tools/pydoclint": ("pydoclint", "python"),
    "tools/pytest": ("pytest", "python"),
    "tools/ruff": ("ruff", "python"),
    "tools/svelte-check": ("svelte-check", "frameworks"),
    "tools/tsc": ("tsc", "js-ts"),
    "tools/vue-tsc": ("vue-tsc", "frameworks"),
    "tools/yamllint": ("yamllint", "config"),
}


ROOT_README_URL = "https://github.com/lgtm-hq/py-lintro"
ROOT_README_LINK = re.compile(
    r"\((?P<ups>(?:\.\./)+)README\.md(?P<hash>#[A-Za-z0-9._-]*)?\)",
)


def rewrite_root_readme_links(body: str, src_dir: str) -> str:
    """Point links that resolve to the repo-root README at the GitHub repo.

    The repo-root README has no equivalent page on the site; GitHub renders it
    (with heading anchors) on the repository home page instead. Only links
    whose ``../`` chain escapes ``docs/`` are rewritten — e.g. ``../README.md``
    from ``docs/README.md`` targets the repo root, while the same link from
    ``docs/architecture/`` targets the docs hub and is left alone.

    Args:
        body: Markdown source being migrated.
        src_dir: Source directory relative to ``docs/`` ("" for the root).

    Returns:
        The body with repo-root README links rewritten.
    """
    depth = len([part for part in src_dir.split("/") if part])

    def _replace(match: re.Match[str]) -> str:
        ups = match.group("ups").count("../")
        if ups != depth + 1:
            return match.group(0)
        return f"({ROOT_README_URL}{match.group('hash') or ''})"

    return ROOT_README_LINK.sub(_replace, body)


def title_from_markdown(text: str, fallback: str) -> str:
    """Extract the first markdown H1 title or return ``fallback``."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return fallback


def description_from_markdown(text: str) -> str:
    """Build a short description from prose after the first H1."""
    lines = text.splitlines()
    started = False
    parts: list[str] = []
    for line in lines:
        if line.startswith("# "):
            started = True
            continue
        if not started:
            continue
        if line.startswith("#"):
            break
        stripped = line.strip()
        if stripped:
            parts.append(stripped)
        if len(" ".join(parts)) > 160:
            break
    desc = " ".join(parts)
    return desc[:200] if desc else ""


def slug_name(path: Path) -> str:
    """Normalize a source path stem into a URL-friendly slug."""
    name = path.stem
    if name == "SHELL-SCRIPT-STYLE-GUIDE":
        return "shell-script-style-guide"
    return name.lower().replace("_", "-")


def nav_meta(dest_rel: str, fallback_title: str) -> tuple[str, str, str | None]:
    """Return (title, navTitle, navGroup) for a destination path."""
    key = dest_rel.removesuffix(".md")
    if key in DOC_NAV:
        short, group = DOC_NAV[key]
        return short, short, group
    return fallback_title, fallback_title, None


def write_doc(
    dest_rel: str,
    category: str,
    order: int,
    body: str,
    title: str,
    *,
    nav_title: str | None = None,
    nav_group: str | None = None,
) -> None:
    """Write a docs content file with Astro frontmatter."""
    dest = DOCS_DEST / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
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


def write_doc_with_nav(
    dest_rel: str,
    category: str,
    order: int,
    body: str,
    fallback_title: str,
) -> None:
    """Write a doc using navigation metadata from :data:`DOC_NAV`."""
    title, nav_title, nav_group = nav_meta(dest_rel, fallback_title)
    write_doc(
        dest_rel,
        category,
        order,
        body,
        title,
        nav_title=nav_title,
        nav_group=nav_group,
    )


def write_route_map(route_map: dict[str, str]) -> None:
    """Write the source→doc route map consumed by the site's link layer.

    The map keys are paths relative to ``docs/`` exactly as authored in
    markdown cross-links (e.g. ``architecture/ARCHITECTURE.md``); values are
    the migrated Astro doc ids (e.g. ``architecture/architecture``). The site
    uses it to rewrite ``.md`` cross-links to final ``/docs/<id>/`` routes.

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
    route_map: dict[str, str] = {}
    if DOCS_DEST.exists():
        for child in DOCS_DEST.iterdir():
            if child.is_dir():
                import shutil

                shutil.rmtree(child)
            else:
                child.unlink()

    for filename, (category, order) in CATEGORY_MAP.items():
        src = DOCS_SRC / filename
        if not src.exists():
            continue
        body = rewrite_root_readme_links(src.read_text(encoding="utf-8"), "")
        slug = slug_name(src)
        fallback = title_from_markdown(body, slug.replace("-", " ").title())
        write_doc_with_nav(f"{category}/{slug}.md", category, order, body, fallback)
        route_map[filename] = f"{category}/{slug}"

    arch_dir = DOCS_SRC / "architecture"
    if arch_dir.is_dir():
        for src in sorted(arch_dir.glob("*.md")):
            body = rewrite_root_readme_links(
                src.read_text(encoding="utf-8"),
                "architecture",
            )
            slug = slug_name(src)
            if slug == "readme":
                slug = "overview"
            order = ARCHITECTURE_ORDER.get(src.name, 100)
            fallback = title_from_markdown(body, slug.replace("-", " ").title())
            write_doc_with_nav(
                f"architecture/{slug}.md",
                "architecture",
                order,
                body,
                fallback,
            )
            route_map[f"architecture/{src.name}"] = f"architecture/{slug}"

    usage_dir = DOCS_SRC / "usage"
    if usage_dir.is_dir():
        for src in sorted(usage_dir.glob("*.md")):
            body = rewrite_root_readme_links(src.read_text(encoding="utf-8"), "usage")
            slug = "index" if src.name == "README.md" else slug_name(src)
            order = USAGE_OVERVIEW_ORDER.get(src.name, 100)
            fallback = title_from_markdown(body, slug.replace("-", " ").title())
            write_doc_with_nav(f"usage/{slug}.md", "usage", order, body, fallback)
            route_map[f"usage/{src.name}"] = f"usage/{slug}"

    sec_dir = DOCS_SRC / "security"
    if sec_dir.is_dir():
        for src in sorted(sec_dir.glob("*.md")):
            body = rewrite_root_readme_links(
                src.read_text(encoding="utf-8"),
                "security",
            )
            slug = slug_name(src)
            if slug == "readme":
                slug = "index"
            order = SECURITY_ORDER.get(src.name, 100)
            fallback = title_from_markdown(body, slug.replace("-", " ").title())
            write_doc_with_nav(f"security/{slug}.md", "security", order, body, fallback)
            route_map[f"security/{src.name}"] = f"security/{slug}"

    tools_dir = DOCS_SRC / "tool-analysis"
    if tools_dir.is_dir():
        order = 20
        for src in sorted(tools_dir.glob("*.md")):
            if src.name == "README.md":
                body = rewrite_root_readme_links(
                    src.read_text(encoding="utf-8"),
                    "tool-analysis",
                )
                write_doc_with_nav(
                    "tools/index.md",
                    "tools",
                    TOOL_ORDER["README.md"],
                    body,
                    "Tools",
                )
                route_map["tool-analysis/README.md"] = "tools/index"
                continue
            body = rewrite_root_readme_links(
                src.read_text(encoding="utf-8"),
                "tool-analysis",
            )
            slug = re.sub(r"-analysis$", "", slug_name(src))
            fallback = slug
            write_doc_with_nav(f"tools/{slug}.md", "tools", order, body, fallback)
            route_map[f"tool-analysis/{src.name}"] = f"tools/{slug}"
            order += 10

    hub = DOCS_SRC / "README.md"
    if hub.exists():
        body = rewrite_root_readme_links(hub.read_text(encoding="utf-8"), "")
        write_doc_with_nav(
            "getting-started/hub.md",
            "getting-started",
            5,
            body,
            "Documentation Hub",
        )
        route_map["README.md"] = "getting-started/hub"

    write_route_map(route_map)
    print(f"Migrated docs to {DOCS_DEST}")
    print(f"Wrote route map to {ROUTE_MAP_DEST}")


if __name__ == "__main__":
    import argparse

    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
