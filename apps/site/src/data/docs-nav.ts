/** Shared docs navigation — sections, section landing pages, and navbar links. */

export const DOC_CATEGORIES = ['start', 'guides', 'ai', 'tools', 'contribute', 'project'] as const;

export type DocCategory = (typeof DOC_CATEGORIES)[number];

export const CATEGORY_LABELS: Record<DocCategory, string> = {
  start: 'Start',
  guides: 'Guides',
  ai: 'AI',
  tools: 'Tools',
  contribute: 'Contribute',
  project: 'Project',
};

/** One-line blurbs for the docs hub and section landing pages. */
export const CATEGORY_BLURBS: Record<DocCategory, string> = {
  start: 'Install lintro, run your first check, and see how it compares to the alternatives.',
  guides: 'Configure tools, run in watch mode, Docker, pre-commit and GitHub Actions.',
  ai: 'Bring-your-own-key summaries, interactive fixes, diff review, and the MCP server.',
  tools: 'Every linter, formatter, type checker and scanner lintro runs, one page each.',
  contribute: 'Add a tool, run the test suite, and follow the style guides.',
  project: 'Architecture, vision, roadmap, decision records, design notes, and security.',
};

/** Landing page id for each section (Astro flattens `<section>/index` to `<section>`). */
export const SECTION_OVERVIEW_ID: Record<DocCategory, string> = {
  start: 'start/overview',
  guides: 'guides',
  ai: 'ai',
  tools: 'tools',
  contribute: 'contribute',
  project: 'project',
};

export function isDocCategory(value: string): value is DocCategory {
  return (DOC_CATEGORIES as readonly string[]).includes(value);
}

export interface DocNavEntry {
  id: string;
  data: {
    title: string;
    navTitle?: string;
    category: DocCategory;
    order: number;
    sidebar?: boolean;
  };
}

/** Resolve a section landing doc, tolerating `guides`, `guides/index` and `start/overview` style ids. */
export function findSectionOverviewDoc<C extends DocNavEntry>(
  docs: C[],
  category: DocCategory
): C | undefined {
  for (const id of sectionOverviewIds(category)) {
    const doc = docs.find((d) => d.id === id);
    if (doc) {
      return doc;
    }
  }

  return undefined;
}

/** Site route for a doc id (index ids are served at their parent path). */
export function docRoute(base: string, docId: string): string {
  const routeId = docId.endsWith('/index') ? docId.slice(0, -'/index'.length) : docId;
  return `${base}docs/${routeId}/`;
}

/** Landing page for a section tab — the section overview, else its first page. */
export function categoryLandingHref(
  docs: DocNavEntry[],
  category: DocCategory,
  base: string
): string {
  const overview = findSectionOverviewDoc(docs, category);
  if (overview) {
    return docRoute(base, overview.id);
  }

  const inCategory = docs
    .filter((d) => d.data.category === category)
    .sort((a, b) => a.data.order - b.data.order);
  if (inCategory.length === 0) {
    return `${base}docs/`;
  }

  return docRoute(base, inCategory[0]!.id);
}

export function sectionDocCount(docs: DocNavEntry[], category: DocCategory): number {
  return docs.filter((d) => d.data.category === category && d.data.sidebar !== false).length;
}

/** Every id that may serve as a section's landing page, most specific first. */
export function sectionOverviewIds(category: DocCategory): string[] {
  return [...new Set([SECTION_OVERVIEW_ID[category], `${category}/index`, category])];
}

export function isSectionOverview(docId: string, category: DocCategory): boolean {
  return sectionOverviewIds(category).includes(docId);
}

export interface NavDropdownItem {
  label: string;
  href: string;
}

export interface NavDropdownGroup {
  label: string;
  href?: string;
  items: NavDropdownItem[];
}

export interface MainNavItem {
  label: string;
  href: string;
  groups: NavDropdownGroup[];
}

/** Sidebar label: the short nav title when the migration provided one. */
export function navLabel(doc: DocNavEntry): string {
  return doc.data.navTitle ?? doc.data.title;
}

function docsInCategory(
  docs: DocNavEntry[],
  category: DocCategory,
  base: string
): NavDropdownItem[] {
  return docs
    .filter(
      (d) =>
        d.data.category === category &&
        d.data.sidebar !== false &&
        !isSectionOverview(d.id, category)
    )
    .sort((a, b) => a.data.order - b.data.order)
    .map((d) => ({
      label: navLabel(d),
      href: docRoute(base, d.id),
    }));
}

/** Dropdown groups for the "Docs" navbar item: one group per section. */
export function docsNavGroups(docs: DocNavEntry[], base: string): NavDropdownGroup[] {
  return DOC_CATEGORIES.map((key) => ({
    label: CATEGORY_LABELS[key],
    href: categoryLandingHref(docs, key, base),
    items: docsInCategory(docs, key, base),
  })).filter((g) => g.items.length > 0);
}

/** Build navbar items with dropdown groups from the docs collection. */
export function buildMainNav(base: string, docs: DocNavEntry[]): MainNavItem[] {
  return [
    {
      label: 'Docs',
      href: `${base}docs/`,
      groups: docsNavGroups(docs, base),
    },
    {
      label: 'Tools',
      href: categoryLandingHref(docs, 'tools', base),
      groups: [],
    },
    {
      label: 'AI',
      href: categoryLandingHref(docs, 'ai', base),
      groups: [],
    },
  ];
}
