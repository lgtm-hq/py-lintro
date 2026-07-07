import { finalizeDescriptionLinks, formatDescriptionLinks } from '../lib/description-links';
import { sanitizeDescriptionHtml } from '../lib/description-html';

export function joinBase(base: string, suffix: string): string {
  const normalizedBase = base.endsWith('/') ? base : `${base}/`;
  return `${normalizedBase}${suffix.replace(/^\//, '')}`;
}

export function asset(base: string, path: string): string {
  return joinBase(base, `assets/images/${path}`);
}

export function publicAsset(base: string, path: string): string {
  return joinBase(base, `assets/${path}`);
}

/** Strip HTML tags for meta descriptions and plain-text fallbacks. */
export function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, '');
}

/** Prefix internal links and preserve normal inline-reading treatment in descriptions. */
export function formatDocDescription(html: string, base: string): string {
  return formatDescriptionLinks(html, base);
}

/** Strip unsafe markup via sanitize-html before set:html (not regex-only). */
export function sanitizeDocDescription(html: string): string {
  const cleaned = sanitizeDescriptionHtml(html);
  return finalizeDescriptionLinks(cleaned);
}
