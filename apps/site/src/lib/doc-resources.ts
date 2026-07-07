import rehypeParse from 'rehype-parse';
import remarkParse from 'remark-parse';
import { unified } from 'unified';
import { isCrossPageLink, isExternalHref, shouldSkipResourceHref } from './doc-link-target.mjs';
import { routeForDocHref } from './doc-route-map';
import { hrefFromProperties } from './description-links';

export interface DocResource {
  label: string;
  href: string;
}

const GENERIC_LABEL = /^(analysis|config guide)$/i;

function titleCaseWords(value: string): string {
  return value
    .split(/[\s-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

function markdownBasename(filePath: string): string {
  const segment = filePath.split('/').pop() ?? filePath;
  return segment.replace(/\.md$/i, '');
}

function labelFromHref(href: string): string | undefined {
  const [path = '', hash] = href.split('#');

  const toolMatch = path.match(/(?:^|\/)tool-analysis\/([a-z0-9-]+)-analysis\.md$/i);
  if (toolMatch?.[1]) {
    return `${titleCaseWords(toolMatch[1])} Analysis`;
  }

  if (hash && /configuration\.md$/i.test(path)) {
    const section = hash.replace(/-configuration$/i, '').replace(/^post-checks.*/i, 'post-checks');
    return `${titleCaseWords(section)} Config`;
  }

  return undefined;
}

function normalizeResourceLabel(label: string, href: string): string {
  let clean = label
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^→\s*/, '');

  const arrowMatch = clean.match(/^(.+?)\s*→\s*(.+)$/);
  if (arrowMatch?.[2]) {
    clean = arrowMatch[2].trim();
  }

  if (/^main\s+readme$/i.test(clean)) {
    return '';
  }

  if (GENERIC_LABEL.test(clean)) {
    const fromHref = labelFromHref(href);
    if (fromHref) {
      return fromHref;
    }
  }

  const hash = href.includes('#') ? href.split('#')[1] : undefined;
  if (hash) {
    const anchor = titleCaseWords(hash.replace(/-(?:configuration|troubleshooting)$/i, ''));
    const pageName = titleCaseWords(markdownBasename(href.split('#')[0] ?? href));
    if (
      anchor &&
      !clean.toLowerCase().includes(anchor.toLowerCase()) &&
      (GENERIC_LABEL.test(clean) || clean.toLowerCase() === pageName.toLowerCase())
    ) {
      return `${clean}: ${anchor}`;
    }
  }

  return clean;
}

interface CollectDocResourcesOptions {
  description?: string;
  markdown?: readonly string[];
  inserted?: readonly DocResource[];
  currentDocId?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function childrenOf(node: unknown): unknown[] {
  if (!isRecord(node) || !Array.isArray(node.children)) {
    return [];
  }

  return node.children;
}

function linkText(node: unknown): string {
  if (!isRecord(node)) {
    return '';
  }

  if (typeof node.value === 'string') {
    return node.value;
  }

  if (typeof node.alt === 'string') {
    return node.alt;
  }

  return childrenOf(node).map(linkText).join('');
}

function createResource(label: string, href: string, currentDocId?: string): DocResource | null {
  const cleanHref = href.trim();

  if (!cleanHref || cleanHref.startsWith('#') || shouldSkipResourceHref(cleanHref)) {
    return null;
  }

  if (currentDocId && !isCrossPageLink(cleanHref, currentDocId)) {
    return null;
  }

  const cleanLabel = normalizeResourceLabel(label, cleanHref);
  if (!cleanLabel) {
    return null;
  }

  // Authored cross-links point at the original docs/ tree; the migration
  // renames those files, so resolve `.md` links and relative directory links
  // (which target a README by markdown convention) to final `docs/<id>/`
  // routes. Links to docs that were not migrated would 404 — drop them.
  let resolvedHref = cleanHref;
  const pathOnly = cleanHref.split('#')[0] ?? '';
  const isMarkdownLink = /\.md$/i.test(pathOnly);
  const isRelativeDirLink = pathOnly.endsWith('/') && !pathOnly.startsWith('/');
  if (currentDocId && (isMarkdownLink || isRelativeDirLink) && !isExternalHref(cleanHref)) {
    const route = routeForDocHref(cleanHref, currentDocId);
    if (!route) {
      return null;
    }
    resolvedHref = route;
  }

  return {
    label: cleanLabel,
    href: resolvedHref,
  };
}

function extractHtmlResources(html: string, currentDocId?: string): DocResource[] {
  const links: DocResource[] = [];
  const tree = unified().use(rehypeParse, { fragment: true }).parse(html);

  function collect(node: unknown): void {
    if (!isRecord(node)) {
      return;
    }

    if (node.tagName === 'a' && isRecord(node.properties)) {
      const href = hrefFromProperties(node.properties.href);
      if (href) {
        const resource = createResource(linkText(node), href, currentDocId);
        if (resource) links.push(resource);
      }
    }

    childrenOf(node).forEach(collect);
  }

  collect(tree);
  return links;
}

function extractMarkdownResources(markdown: string, currentDocId?: string): DocResource[] {
  const links: DocResource[] = [];
  const tree = unified().use(remarkParse).parse(markdown);

  function collect(node: unknown): void {
    if (!isRecord(node)) {
      return;
    }

    if (node.type === 'link' && typeof node.url === 'string') {
      const resource = createResource(linkText(node), node.url, currentDocId);
      if (resource) links.push(resource);
      return;
    }

    if (node.type === 'html' && typeof node.value === 'string') {
      links.push(...extractHtmlResources(node.value, currentDocId));
    }

    childrenOf(node).forEach(collect);
  }

  collect(tree);
  return links;
}

export function dedupeResources(resources: readonly DocResource[]): DocResource[] {
  const seen = new Set<string>();

  return resources.filter(({ href }) => {
    if (seen.has(href)) {
      return false;
    }

    seen.add(href);
    return true;
  });
}

export function collectDocResources({
  description = '',
  markdown = [],
  inserted = [],
  currentDocId,
}: CollectDocResourcesOptions): DocResource[] {
  const normalizedInserted = inserted
    .map((item) => createResource(item.label, item.href, currentDocId))
    .filter((item): item is DocResource => item !== null);

  return dedupeResources([
    ...extractHtmlResources(description, currentDocId),
    ...normalizedInserted,
    ...markdown.flatMap((entry) => extractMarkdownResources(entry, currentDocId)),
  ]);
}
