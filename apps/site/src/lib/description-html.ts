import sanitizeHtml from 'sanitize-html';

function relForBlankTarget(rel: string | undefined): string {
  const tokens = new Set<string>();
  if (rel) {
    rel
      .split(/\s+/)
      .filter(Boolean)
      .forEach((token) => tokens.add(token));
  }
  tokens.add('noopener');
  tokens.add('noreferrer');
  return [...tokens].join(' ');
}

/** Options for doc frontmatter description HTML rendered via set:html. */
export const DESCRIPTION_HTML_OPTIONS: sanitizeHtml.IOptions = {
  allowedTags: ['a', 'code'],
  allowedAttributes: {
    a: ['href', 'class', 'target', 'rel'],
    code: [],
  },
  allowedSchemes: ['http', 'https', 'mailto'],
  allowProtocolRelative: false,
  disallowedTagsMode: 'discard',
  transformTags: {
    a: (_tagName, attribs) => {
      if (attribs.target === '_blank') {
        return {
          tagName: 'a',
          attribs: {
            ...attribs,
            rel: relForBlankTarget(attribs.rel),
          },
        };
      }
      return { tagName: 'a', attribs };
    },
  },
};

/** Strip unsafe markup while preserving allowed inline doc description tags. */
export function sanitizeDescriptionHtml(html: string): string {
  return sanitizeHtml(html, DESCRIPTION_HTML_OPTIONS);
}
