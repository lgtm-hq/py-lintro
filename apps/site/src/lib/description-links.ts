import type { Element, Properties, Root } from "hast";
import rehypeParse from "rehype-parse";
import rehypeStringify from "rehype-stringify";
import { unified } from "unified";
import { visit } from "unist-util-visit";

const UNSAFE_HREF = /^\s*(?:javascript:|data:)/i;

/** Normalize hast href properties (string or string[]). */
export function hrefFromProperties(href: unknown): string | undefined {
  if (typeof href === "string") {
    return href;
  }
  if (Array.isArray(href) && typeof href[0] === "string") {
    return href[0];
  }
  return undefined;
}

function hrefValue(node: Element): string | undefined {
  return hrefFromProperties(node.properties?.href);
}

function setHref(node: Element, href: string): void {
  node.properties = { ...node.properties, href };
}

function mergeRel(node: Element): void {
  const target = node.properties?.target;
  const hasTarget = target === "_blank" || (Array.isArray(target) && target.includes("_blank"));
  if (!hasTarget) {
    return;
  }

  const existing = node.properties?.rel;
  const tokens = new Set<string>();
  if (typeof existing === "string") {
    existing
      .split(/\s+/)
      .filter(Boolean)
      .forEach((t) => tokens.add(t));
  } else if (Array.isArray(existing)) {
    existing
      .flatMap((v) => String(v).split(/\s+/))
      .filter(Boolean)
      .forEach((t) => tokens.add(t));
  }
  tokens.add("noopener");
  tokens.add("noreferrer");
  node.properties = { ...node.properties, rel: [...tokens].join(" ") };
}

function transformFormatAnchors(tree: Root, base: string): void {
  const baseNoSlash = base.endsWith("/") ? base.slice(0, -1) : base;

  visit(tree, "element", (node) => {
    if (node.tagName !== "a") {
      return;
    }

    const href = hrefValue(node);
    if (!href || UNSAFE_HREF.test(href)) {
      return;
    }

    if (/^https?:\/\//i.test(href)) {
      setHref(node, href);
      const target = node.properties?.target;
      const hasTarget = target === "_blank" || (Array.isArray(target) && target.includes("_blank"));
      if (hasTarget) {
        mergeRel(node);
      } else {
        node.properties = {
          ...node.properties,
          target: "_blank",
          rel: "noopener noreferrer",
        };
      }
      return;
    }

    if (href.startsWith("#") || href.startsWith("mailto:")) {
      return;
    }

    const isResolved = href === baseNoSlash || href.startsWith(`${baseNoSlash}/`);
    const resolvedHref = isResolved
      ? href
      : href.startsWith("/")
        ? `${baseNoSlash}${href}`
        : `${baseNoSlash}/${href.replace(/^\//, "")}`;
    setHref(node, resolvedHref);
  });
}

function transformSanitizeAnchors(tree: Root): void {
  visit(tree, "element", (node, index, parent) => {
    if (node.tagName !== "a" || !parent || index === undefined) {
      return;
    }

    const href = hrefValue(node);
    if (!href || UNSAFE_HREF.test(href)) {
      parent.children.splice(index, 1, ...(node.children ?? []));
      return;
    }

    const allowed: Properties = { href };
    const source = node.properties ?? {};
    if (typeof source.class === "string" || Array.isArray(source.class)) {
      allowed.class = source.class;
    }
    if (typeof source.target === "string" || Array.isArray(source.target)) {
      allowed.target = source.target;
    }
    if (typeof source.rel === "string" || Array.isArray(source.rel)) {
      allowed.rel = source.rel;
    }
    node.properties = allowed;
  });
}

function rehypeApply(transform: (tree: Root) => void) {
  return (tree: Root) => {
    transform(tree);
  };
}

function processDescriptionHtml(html: string, transform: (tree: Root) => void): string {
  const processor = unified()
    .use(rehypeParse, { fragment: true })
    .use(rehypeApply, transform)
    .use(rehypeStringify);
  return String(processor.processSync(html));
}

/** Prefix internal links and add safe external link attributes in description HTML. */
export function formatDescriptionLinks(html: string, base: string): string {
  return processDescriptionHtml(html, (tree) => transformFormatAnchors(tree, base));
}

/** Normalize anchors after sanitize-html (drop unsafe links). */
export function finalizeDescriptionLinks(html: string): string {
  return processDescriptionHtml(html, transformSanitizeAnchors);
}
