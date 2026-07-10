import { hasUriScheme } from './url-utils';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/** Hostname for remote favicon lookup (no port, no leading www.). */
export function faviconDomain(href: string): string | undefined {
  if (!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:')) {
    return undefined;
  }

  try {
    const normalized = href.startsWith('//') ? `https:${href}` : href;
    if (!hasUriScheme(normalized) && !href.startsWith('//')) {
      return undefined;
    }

    const { hostname, protocol } = new URL(normalized);
    if (protocol !== 'http:' && protocol !== 'https:') {
      return undefined;
    }

    const host = hostname.replace(/^www\./i, '');
    if (!host || LOCAL_HOSTS.has(host)) {
      return undefined;
    }

    return host;
  } catch {
    return undefined;
  }
}

/** Google favicon service URL for a public http(s) host, if applicable. */
export function remoteFaviconUrl(href: string): string | undefined {
  const domain = faviconDomain(href);
  if (!domain) {
    return undefined;
  }

  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`;
}
