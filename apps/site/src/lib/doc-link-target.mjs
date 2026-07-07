const README_HREF = /(?:^|\/)(?:\.\.\/)*README\.md(?:#|$)/i;

/**
 * @param {string} path
 * @returns {string | undefined}
 */
export function docIdFromVFilePath(path) {
  if (!path || typeof path !== "string") {
    return undefined;
  }

  const normalized = path.replace(/\\/g, "/");
  const marker = "/content/docs/";
  const idx = normalized.indexOf(marker);
  if (idx === -1) {
    return undefined;
  }

  return normalized
    .slice(idx + marker.length)
    .replace(/\.mdx?$/i, "")
    .replace(/\/index$/i, "");
}

/**
 * @param {string} docId
 * @returns {string}
 */
export function docDirectory(docId) {
  if (docId.includes("/")) {
    return docId.slice(0, docId.lastIndexOf("/"));
  }

  return docId;
}

/**
 * @param {string} path
 * @returns {string}
 */
export function normalizeDocPath(path) {
  const parts = path.split("/").filter(Boolean);
  /** @type {string[]} */
  const stack = [];

  for (const part of parts) {
    if (part === ".") {
      continue;
    }

    if (part === "..") {
      stack.pop();
      continue;
    }

    stack.push(part);
  }

  return stack.join("/");
}

/**
 * @param {string} href
 * @returns {boolean}
 */
export function isExternalHref(href) {
  if (href.startsWith("//")) {
    return true;
  }

  if (!href.startsWith("http://") && !href.startsWith("https://")) {
    return false;
  }

  try {
    const { hostname } = new URL(href);
    return hostname !== "localhost" && hostname !== "127.0.0.1" && hostname !== "[::1]";
  } catch {
    return false;
  }
}

/**
 * @param {string} href
 * @returns {boolean}
 */
export function shouldSkipResourceHref(href) {
  return README_HREF.test(href);
}

/**
 * @param {string} href
 * @param {string} currentDocId
 * @returns {string | "external"}
 */
export function resolveTargetDocId(href, currentDocId) {
  const trimmed = href.trim();
  if (!trimmed || trimmed.startsWith("#")) {
    return currentDocId;
  }

  const hashIndex = trimmed.indexOf("#");
  const pathPart = hashIndex === -1 ? trimmed : trimmed.slice(0, hashIndex);
  if (!pathPart) {
    return currentDocId;
  }

  if (isExternalHref(pathPart)) {
    return "external";
  }

  if (pathPart.startsWith("mailto:") || pathPart.startsWith("tel:")) {
    return currentDocId;
  }

  let resolved = pathPart;
  if (!pathPart.startsWith("/") && !pathPart.includes("://")) {
    resolved = normalizeDocPath(`${docDirectory(currentDocId)}/${pathPart}`);
  } else if (pathPart.startsWith("/")) {
    resolved = pathPart.replace(/^\//, "");
  }

  return resolved
    .replace(/\.md$/i, "")
    .replace(/\/index$/i, "")
    .replace(/\/$/, "");
}

/**
 * @param {string} href
 * @param {string} currentDocId
 * @returns {boolean}
 */
export function isCrossPageLink(href, currentDocId) {
  if (!href?.trim() || href.startsWith("#") || shouldSkipResourceHref(href)) {
    return false;
  }

  const target = resolveTargetDocId(href, currentDocId);
  if (target === "external") {
    return true;
  }

  return target !== currentDocId;
}

/**
 * @param {import('unist').Node} node
 * @returns {string}
 */
export function markdownLinkText(node) {
  if (!node || typeof node !== "object") {
    return "";
  }

  if ("value" in node && typeof node.value === "string") {
    return node.value;
  }

  if ("children" in node && Array.isArray(node.children)) {
    return node.children.map(markdownLinkText).join("");
  }

  return "";
}
