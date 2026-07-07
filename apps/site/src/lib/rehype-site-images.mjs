/** @typedef {import('hast').Root} Root */
/** @typedef {import('hast').Element} Element */

const SITE_IMAGES_PREFIX = "/assets/images/";

/** @param {string} value */
function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const escapedPrefix = escapeRegExp(SITE_IMAGES_PREFIX);
// Case-insensitive src= matching handles nonstandard HTML from markdown/rehype output.
const SRC_ATTR_PATTERN = new RegExp(
  String.raw`src\s*=\s*(["'])${escapedPrefix}((?:\\.|[^'"\\])*)\1|src\s*=\s*${escapedPrefix}([^\s>]+)`,
  "gi",
);

/**
 * Prefix public image paths with the Astro base URL for GitHub Pages subpath deploys.
 *
 * @param {string} basePath
 * @returns {(tree: Root) => void}
 */
export function rehypeSiteImages(basePath) {
  const base = basePath.endsWith("/") ? basePath : `${basePath}/`;
  const prefixed = `${base}${SITE_IMAGES_PREFIX.slice(1)}`;

  return (tree) => {
    walk(tree, (node) => {
      if (node.type === "raw" && typeof node.value === "string") {
        node.value = node.value.replace(SRC_ATTR_PATTERN, (match, quote, quotedPath, barePath) => {
          const path = quotedPath ?? barePath;
          if (quote) {
            return `src=${quote}${prefixed}${path}${quote}`;
          }

          return `src=${prefixed}${path}`;
        });
        return;
      }

      if (node.type !== "element" || node.tagName !== "img") {
        return;
      }

      const src = node.properties?.src;
      if (typeof src !== "string" || !src.startsWith(SITE_IMAGES_PREFIX)) {
        return;
      }

      node.properties.src = `${base}${src.slice(1)}`;
    });
  };
}

/**
 * @param {import('hast').Root | import('hast').Element | import('hast').RootContent} node
 * @param {(node: import('hast').Element | import('hast').RootContent & { type: 'raw' }) => void} visit
 */
function walk(node, visit) {
  if (node.type === "element" || node.type === "raw") {
    visit(node);
  }

  if ("children" in node && Array.isArray(node.children)) {
    for (const child of node.children) {
      walk(child, visit);
    }
  }
}
