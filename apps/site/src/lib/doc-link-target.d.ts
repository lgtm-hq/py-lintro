export function docIdFromVFilePath(path: string): string | undefined;
export function docDirectory(docId: string): string;
export function normalizeDocPath(path: string): string;
export function isExternalHref(href: string): boolean;
export function shouldSkipResourceHref(href: string): boolean;
export function resolveTargetDocId(href: string, currentDocId: string): string | "external";
export function isCrossPageLink(href: string, currentDocId: string): boolean;
export function markdownLinkText(node: unknown): string;
