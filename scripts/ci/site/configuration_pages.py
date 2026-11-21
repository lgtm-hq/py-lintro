"""Split configuration by task and tool while preserving public heading anchors."""

from __future__ import annotations

import re
from collections.abc import Callable
from html import escape
from typing import Any

HUB = "/docs/guides/configuration/"
TASK_STARTS = {
    "configuration-model-5-tier-system": "precedence",
    "lintro-configuration": "core-settings",
    "configuration-report-command": "reports",
    "command-line-options": "cli-and-environment",
    "external-plugins-trust-model": "plugins",
    "pre-execution-summary": "execution",
    "project-setup-with-lintro-init": "setup-and-install",
    "tool-configuration": "tool-policy",
    "tool-ordering-configuration": "tool-ordering",
    "nodejs-tool-resolution": "node-resolution",
    "python-tools": "tools",
    "project-specific-configuration": "project-examples",
    "ai-configuration": "ai",
    "advanced-configuration": "advanced",
    "integration-patterns": "integrations",
    "packaged-semgrep-rules": "semgrep-rules",
    "troubleshooting-configuration": "troubleshooting",
}


def heading_slug(text: str) -> str:
    """Match the ASCII configuration heading slugs emitted by Astro.

    Args:
        text: Authored Markdown heading.

    Returns:
        Explicit id when present, otherwise a punctuation-stripped slug.
    """
    explicit = re.search(r"\{#([\w.-]+)\}\s*$", text)
    if explicit:
        return explicit[1]
    text = re.sub(r"<[^>]+>", "", text).lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def split_configuration(body: str, write_doc: Callable[..., None]) -> dict[str, Any]:
    """Write focused pages, keeping every source line and inbound heading.

    Args:
        body: Configuration source after README link normalization.
        write_doc: Existing migration writer rooted in the candidate.

    Returns:
        Page records and old-fragment to precise destination map.
    """
    pages: dict[str, dict[str, Any]] = {}
    anchors: dict[str, str] = {}
    labels: dict[str, str] = {}
    counts: dict[str, int] = {}
    base_levels: dict[str, int] = {}
    current = "intro"
    pages[current] = {"title": "Configuration", "lines": []}
    in_fence = False
    tools_started = False
    for line in body.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = re.match(r"^(#{2,6}) (.+?)\s*$", line) if not in_fence else None
        if match:
            level, text = len(match[1]), match[2]
            title = re.sub(r"\s*\{#[\w.-]+\}\s*$", "", text).replace("`", "")
            original = heading_slug(text)
            count = counts.get(original, 0)
            counts[original] = count + 1
            anchor = f"{original}-{count}" if count else original
            if original in TASK_STARTS:
                current = TASK_STARTS[original]
                tools_started = current == "tools"
            elif tools_started and (level == 4 or original == "buf-configuration"):
                current = "tools/" + original.removesuffix("-configuration")
            elif tools_started and level == 3:
                current = "tools"
            pages.setdefault(current, {"title": title, "lines": []})
            if current.startswith("tools/"):
                pages["tools"]["lines"].append(f"\n- [{title}]({HUB}{current}/)\n")
            pages[current].setdefault("anchor", anchor)
            labels[anchor] = title
            anchors[anchor] = f"{HUB}{current}/#{anchor}"
            base_levels.setdefault(current, level)
            tag = f"h{min(6, max(2, level - base_levels[current] + 1))}"
            line = f'<{tag} id="{anchor}">{escape(title)}</{tag}>\n'
        pages[current]["lines"].append(line)

    return write_configuration_pages(pages, anchors, labels, write_doc)


def write_configuration_pages(
    pages: dict[str, dict[str, Any]],
    anchors: dict[str, str],
    labels: dict[str, str],
    write_doc: Callable[..., None],
) -> dict[str, Any]:
    """Render the split pages and their hub from the parsed source sections.

    Args:
        pages: Ordered source sections and their original heading anchors.
        anchors: Legacy fragment destinations.
        labels: Human-readable section labels.
        write_doc: Existing migration writer.

    Returns:
        The generated navigation and forwarding manifest.
    """

    def rewrite_links(text: str) -> str:
        """Forward local source anchors to their precise split destinations."""
        return re.sub(
            r"\]\(#([^\s)]+)\)",
            lambda m: f"]({anchors.get(m[1], '#' + m[1])})",
            text,
        )

    if "tools" in pages:
        pages["tools"]["title"] = "Tool settings directory"
    records = []
    for slug, page in pages.items():
        if slug == "intro":
            continue
        records.append(
            {
                "slug": slug,
                "title": page["title"],
                "href": f"{HUB}{slug}/",
                "titleAnchor": page["anchor"] if slug != "tools" else "",
            },
        )
        lines = page["lines"] if slug == "tools" else page["lines"][1:]
        text = rewrite_links("".join(lines))
        write_doc(
            f"guides/configuration/{slug}.md",
            "guides",
            11,
            (
                f"[← Configuration hub]({HUB})\n\n{text}\n\n"
                f"[Back to configuration]({HUB})\n"
            ),
            page["title"],
            description=(
                f"Lintro configuration: {page['title']}. "
                "Settings, examples and source reference."
            ),
        )
    tasks = [p for p in records if not p["slug"].startswith("tools/")]
    hub = (
        "Choose the setting you need. Start with precedence, then core settings; "
        "keep native tool configuration alongside your project.\n\n"
        "## Configure your workflow\n\n"
        + "\n".join(f"- [{p['title']}]({p['href']})" for p in tasks)
        + "\n\n## Configure a tool\n\n"
        + "\n".join(
            f"- [{p['title']}]({p['href']})"
            for p in records
            if p["slug"].startswith("tools/")
        )
        + "\n\n## Source introduction\n\n"
        + rewrite_links("".join(pages["intro"]["lines"]))
    )
    write_doc(
        "guides/configuration.md",
        "guides",
        10,
        hub,
        "Configuration Guide",
        nav_title="Configuration",
        nav_group="setup",
        description=(
            "Configure Lintro by task: precedence, core settings, "
            "reports, AI and individual tools."
        ),
    )
    return {"pages": records, "anchors": anchors, "labels": labels}
