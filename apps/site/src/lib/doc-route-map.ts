import { isExternalHref, normalizeDocPath } from "./doc-link-target.mjs";
import { sourceToDoc } from "../generated/docs-route-map";

/**
 * Authored docs cross-link resolution.
 *
 * Docs markdown is written against the repo-root docs/ tree, but the migration
 * (scripts/ci/site/migrate-docs-content.py) renames and re-categorizes files
 * (e.g. architecture/ARCHITECTURE.md → architecture/architecture,
 * configuration.md → usage/configuration). This module resolves the authored
 * `.md` hrefs to final `/docs/<id>/` routes using the generated source→doc map.
 */

const defaultDocToSource: Record<string, string> = invertMap(sourceToDoc);

function invertMap(map: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(map).map(([source, docId]) => [docId, source]));
}

/** The docs/-relative source path a migrated doc id was generated from. */
export function sourceForDocId(
  docId: string,
  map: Record<string, string> = sourceToDoc,
): string | undefined {
  const docToSource = map === sourceToDoc ? defaultDocToSource : invertMap(map);
  return docToSource[docId];
}

/**
 * Resolve an authored docs cross-link to its final site route.
 *
 * Handles `.md` file links and directory links (`tool-analysis/`), which by
 * markdown convention target that directory's README. Returns a base-relative
 * route like `docs/usage/configuration/#hash`, or null when the href is
 * external, not a docs link, or does not point at a migrated doc (callers
 * should drop such links rather than emit them).
 */
export function routeForDocHref(
  href: string,
  currentDocId: string,
  map: Record<string, string> = sourceToDoc,
): string | null {
  const trimmed = href.trim();
  if (!trimmed || isExternalHref(trimmed)) {
    return null;
  }

  const hashIndex = trimmed.indexOf("#");
  const pathPart = hashIndex === -1 ? trimmed : trimmed.slice(0, hashIndex);
  const hash = hashIndex === -1 ? "" : trimmed.slice(hashIndex);

  const isMarkdownLink = /\.md$/i.test(pathPart);
  const isDirectoryLink = pathPart.endsWith("/");
  if (!isMarkdownLink && !isDirectoryLink) {
    return null;
  }

  let sourceRel: string;
  if (pathPart.startsWith("/")) {
    sourceRel = normalizeDocPath(pathPart);
  } else {
    const source = sourceForDocId(currentDocId, map);
    if (!source) {
      return null;
    }
    const dir = source.includes("/") ? source.slice(0, source.lastIndexOf("/")) : "";
    sourceRel = normalizeDocPath(dir ? `${dir}/${pathPart}` : pathPart);
  }
  if (!sourceRel) {
    return null;
  }

  const docId = map[isMarkdownLink ? sourceRel : `${sourceRel}/README.md`];
  if (!docId) {
    return null;
  }

  // Astro serves `<section>/index` ids at the parent route (`docs/<section>/`).
  const routeId = docId.endsWith("/index") ? docId.slice(0, -"/index".length) : docId;
  return `docs/${routeId}/${hash}`;
}
