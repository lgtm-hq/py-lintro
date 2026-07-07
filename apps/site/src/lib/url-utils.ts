const URI_SCHEME_PATTERN = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

/** True when href uses a URI scheme or protocol-relative form. */
export function hasUriScheme(href: string): boolean {
  return URI_SCHEME_PATTERN.test(href) || href.startsWith("//");
}
