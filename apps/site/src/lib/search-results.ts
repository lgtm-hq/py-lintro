export interface SearchData {
  url: string;
  meta: { title?: string };
  excerpt: string;
}
export interface SearchHit {
  id: string;
  data: () => Promise<SearchData>;
}
export interface SearchIndex {
  destroy?: () => Promise<void>;
  search: (term: string) => Promise<{ results: SearchHit[] }>;
}

/** Promote direct page-title lookups without disturbing other relevance ordering. */
export function prioritizeTitles(items: SearchData[], term: string): SearchData[] {
  const normalize = (value: string): string =>
    value
      .toLocaleLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, ' ')
      .trim();
  const query = normalize(term);
  if (!query) return items;
  const matches = (item: SearchData): boolean => {
    const title = normalize(item.meta.title ?? '');
    return title === query || title === `${query} guide` || title === `${query} reference`;
  };
  return [...items.filter(matches), ...items.filter((item) => !matches(item))];
}

/** Keep Pagefind's relevance order while requiring whole-word matches by default. */
export async function findResults(
  index: SearchIndex,
  term: string,
  partial: boolean
): Promise<SearchHit[]> {
  const ranked = await index.search(term);
  if (partial || term.includes('"')) return ranked.results;
  const words = term
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => `"${word}"`)
    .join(' ');
  const whole = await index.search(words);
  const accepted = new Set(whole.results.map((hit) => hit.id));
  return ranked.results.filter((hit) => accepted.has(hit.id));
}
