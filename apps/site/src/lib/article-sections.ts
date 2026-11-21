export interface ArticleHeading {
  depth: number;
  slug: string;
  text: string;
}

export interface ArticleSection extends ArticleHeading {
  children: ArticleSection[];
}

/** Build semantic nested lists even when a document starts below H2. */
export function articleSections(headings: ArticleHeading[]): ArticleSection[] {
  const roots: ArticleSection[] = [];
  const stack: ArticleSection[] = [];
  for (const heading of headings) {
    if (heading.depth < 2 || heading.depth > 4) continue;
    while (stack.length && stack.at(-1)!.depth >= heading.depth) stack.pop();
    const section = { ...heading, children: [] };
    (stack.at(-1)?.children ?? roots).push(section);
    stack.push(section);
  }
  return roots;
}
