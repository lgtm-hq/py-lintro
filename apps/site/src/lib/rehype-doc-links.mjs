import { posix } from 'node:path';
import { docIdFromVFilePath, shouldSkipResourceHref } from './doc-link-target.mjs';
import { routeForDocHref } from './doc-route-map';

/**
 * Repo-root README links have no equivalent page on the site; GitHub renders
 * the README (with heading anchors) on the repository home page instead.
 */
const REPO_URL = 'https://github.com/lgtm-hq/py-lintro';

/** @typedef {import('hast').Root} Root */
/** @typedef {import('hast').Element} Element */
/** @typedef {import('hast').Text} Text */

/**
 * Strip link markup from heading elements — headings should be plain text only.
 *
 * @returns {(tree: Root) => void}
 */
export function rehypeUnwrapHeadingLinks() {
  return (tree) => {
    walk(tree, [], (node) => {
      if (node.type !== 'element' || !/^h[1-6]$/.test(node.tagName)) {
        return;
      }

      node.children = unwrapAnchorChildren(node.children ?? []);
    });
  };
}

/**
 * Resolve internal markdown paths and make external prose links open safely.
 *
 * Authored `.md` cross-links (and `dir/` links, which target a README) are
 * resolved through the generated route map to their final `/docs/<id>/`
 * route. A link whose target was not migrated is unwrapped to plain text so
 * the page never ships a 404.
 *
 * @param {string} basePath
 * @returns {(tree: Root, file?: import('vfile').VFile) => void}
 */
export function rehypeDocLinks(basePath) {
  const base = basePath.endsWith('/') ? basePath : `${basePath}/`;
  const baseNoSlash = base.endsWith('/') ? base.slice(0, -1) : base;

  return (tree, file) => {
    const docId = docIdFromVFilePath(file?.history?.[0] ?? '');

    walk(tree, [], (node, ancestors) => {
      if (node.type !== 'element' || node.tagName !== 'a') {
        return;
      }

      if (ancestors.some((ancestor) => ancestor.tagName === 'pre')) {
        return;
      }

      if (ancestors.some((ancestor) => /^h[1-6]$/.test(ancestor.tagName))) {
        return;
      }

      const href = node.properties?.href;
      if (typeof href !== 'string') {
        return;
      }

      if (shouldSkipResourceHref(href)) {
        const hashIndex = href.indexOf('#');
        const hash = hashIndex === -1 ? '' : href.slice(hashIndex);
        node.properties.href = `${REPO_URL}${hash}`;
        node.properties.target = '_blank';
        node.properties.rel = 'noopener noreferrer';
        return;
      }

      if (isExternal(href)) {
        node.properties.target = '_blank';
        node.properties.rel = 'noopener noreferrer';
        return;
      }

      if (docId && isAuthoredDocLink(href)) {
        const route = routeForDocHref(href, docId);
        if (route) {
          node.properties.href = `${base}${route}`;
          return;
        }
        replaceWithText(node, ancestors);
        return;
      }

      if (isInternal(href, baseNoSlash)) {
        node.properties.href = withBase(href, baseNoSlash);
      }
    });
  };
}

/**
 * A relative `.md` link or a relative `dir/` link authored against docs/.
 *
 * @param {string} href
 * @returns {boolean}
 */
function isAuthoredDocLink(href) {
  const pathPart = href.split('#')[0] ?? '';
  if (!pathPart || pathPart.startsWith('/') || pathPart.includes('://')) {
    return false;
  }
  return /\.md$/i.test(pathPart) || pathPart.endsWith('/');
}

/**
 * @param {Element} node
 * @param {Element[]} ancestors
 */
function replaceWithText(node, ancestors) {
  const parent = ancestors.at(-1);
  const siblings = parent?.children;
  if (!parent || !Array.isArray(siblings)) {
    return;
  }
  const index = siblings.indexOf(node);
  if (index !== -1) {
    siblings[index] = { type: 'text', value: linkText(node) };
  }
}

/**
 * @param {string} href
 * @returns {boolean}
 */
function isExternal(href) {
  if (href.startsWith('//')) {
    return true;
  }

  if (!href.startsWith('http://') && !href.startsWith('https://')) {
    return false;
  }

  try {
    const { hostname } = new URL(href);
    return hostname !== 'localhost' && hostname !== '127.0.0.1' && hostname !== '[::1]';
  } catch {
    return false;
  }
}

/**
 * @param {string} href
 * @param {string} baseNoSlash
 * @returns {boolean}
 */
function isInternal(href, baseNoSlash) {
  if (
    href.startsWith('//') ||
    href.startsWith('#') ||
    href.startsWith('mailto:') ||
    href.startsWith('tel:')
  ) {
    return false;
  }

  if (href.startsWith('/')) {
    return !href.startsWith(`${baseNoSlash}/assets/`);
  }

  return !href.includes('://');
}

/**
 * @param {string} href
 * @param {string} baseNoSlash
 * @returns {string}
 */
function withBase(href, baseNoSlash) {
  if (href.startsWith(`${baseNoSlash}/`) || href === baseNoSlash) {
    return href;
  }

  const joined = href.startsWith('/')
    ? posix.join(baseNoSlash, href)
    : posix.join(baseNoSlash, href.replace(/^\//, ''));

  const normalized = posix.normalize(joined);
  if (normalized === baseNoSlash || normalized.startsWith(`${baseNoSlash}/`)) {
    return normalized;
  }

  if (href.startsWith('/')) {
    return `${baseNoSlash}${href}`;
  }

  return `${baseNoSlash}/${href.replace(/^\//, '')}`;
}

/**
 * @param {import('hast').RootContent[]} nodes
 * @returns {import('hast').PhrasingContent[]}
 */
function unwrapAnchorChildren(nodes) {
  /** @type {import('hast').PhrasingContent[]} */
  const result = [];

  for (const node of nodes) {
    if (node.type === 'element' && node.tagName === 'a') {
      const text = linkText(node);
      if (text) {
        result.push({ type: 'text', value: text });
      }
      continue;
    }

    if (node.type === 'element') {
      result.push({
        ...node,
        children: unwrapAnchorChildren(node.children ?? []),
      });
      continue;
    }

    if (node.type === 'text') {
      result.push(node);
    }
  }

  return result;
}

/**
 * @param {Element} node
 * @returns {string}
 */
function linkText(node) {
  return collectText(node.children ?? []);
}

/**
 * @param {import('hast').RootContent[]} nodes
 * @returns {string}
 */
function collectText(nodes) {
  return nodes
    .map((node) => {
      if (node.type === 'text') {
        return node.value;
      }

      if (node.type === 'element') {
        return collectText(node.children ?? []);
      }

      return '';
    })
    .join('');
}

/**
 * @param {import('hast').Root | import('hast').RootContent} node
 * @param {Element[]} ancestors
 * @param {(node: Element, ancestors: Element[]) => void} visit
 */
function walk(node, ancestors, visit) {
  if (node.type === 'element') {
    visit(node, ancestors);
  }

  if ('children' in node && Array.isArray(node.children)) {
    const nextAncestors = node.type === 'element' ? [...ancestors, node] : ancestors;

    for (const child of node.children) {
      walk(child, nextAncestors, visit);
    }
  }
}
