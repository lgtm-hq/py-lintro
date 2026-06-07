/** Shared docs navigation — categories, sidebar groups, and navbar links. */

export const DOC_CATEGORIES = [
  "getting-started",
  "usage",
  "tools",
  "architecture",
  "security",
  "contributing",
] as const;

export type DocCategory = (typeof DOC_CATEGORIES)[number];

export const STANDALONE_DOC_CATEGORIES: readonly DocCategory[] = [] as const;

export const CORE_DOC_CATEGORIES = DOC_CATEGORIES.filter(
  (c) => !STANDALONE_DOC_CATEGORIES.includes(c),
);

export const CATEGORY_LABELS: Record<DocCategory, string> = {
  "getting-started": "Getting Started",
  usage: "Usage",
  tools: "Tools",
  architecture: "Architecture",
  security: "Security",
  contributing: "Contributing",
};

export function isStandaloneCategory(category: DocCategory): boolean {
  return STANDALONE_DOC_CATEGORIES.includes(category);
}

/** Short labels for compact section tabs (dual-rail style). */
export const CATEGORY_TAB_LABELS: Record<DocCategory, string> = {
  "getting-started": "start",
  usage: "usage",
  tools: "tools",
  architecture: "arch",
  security: "sec",
  contributing: "contrib",
};

/** Default landing page when a section tab is selected. */
export const SECTION_OVERVIEW_ID: Record<DocCategory, string> = {
  "getting-started": "getting-started/hub",
  usage: "usage",
  tools: "tools",
  architecture: "architecture/overview",
  security: "security",
  contributing: "contributing/contributing",
};

/** Resolve a section overview doc (Astro uses `usage` not `usage/index` for index.md). */
export function findSectionOverviewDoc<C extends DocNavEntry>(
  docs: C[],
  category: DocCategory,
): C | undefined {
  const candidates = [SECTION_OVERVIEW_ID[category], `${category}/index`, category];
  const seen = new Set<string>();

  for (const id of candidates) {
    if (seen.has(id)) {
      continue;
    }
    seen.add(id);
    const doc = docs.find((d) => d.id === id);
    if (doc) {
      return doc;
    }
  }

  return undefined;
}

/** Landing page for a category tab — uses each section's overview page. */
export function categoryLandingHref(
  docs: DocNavEntry[],
  category: DocCategory,
  base: string,
): string {
  const overview = findSectionOverviewDoc(docs, category);
  if (overview) {
    return `${base}docs/${overview.id}/`;
  }

  const inCategory = docs
    .filter((d) => d.data.category === category)
    .sort((a, b) => a.data.order - b.data.order);
  if (inCategory.length === 0) {
    return `${base}docs/`;
  }

  return `${base}docs/${inCategory[0]!.id}/`;
}

export function sectionDocCount(docs: DocNavEntry[], category: DocCategory): number {
  return docs.filter((d) => d.data.category === category && d.data.sidebar !== false).length;
}

export interface NavDropdownItem {
  label: string;
  href: string;
}

export interface NavDropdownGroup {
  label: string;
  items: NavDropdownItem[];
}

export interface MainNavItem {
  label: string;
  href: string;
  groups: NavDropdownGroup[];
}

export interface DocNavEntry {
  id: string;
  data: {
    title: string;
    category: DocCategory;
    order: number;
    sidebar?: boolean;
  };
}

function docsInCategory(
  docs: DocNavEntry[],
  category: DocCategory,
  base: string,
): NavDropdownItem[] {
  return docs
    .filter((d) => d.data.category === category)
    .sort((a, b) => a.data.order - b.data.order)
    .map((d) => ({
      label: d.data.title,
      href: `${base}docs/${d.id}/`,
    }));
}

function groupsForCategories(
  docs: DocNavEntry[],
  categories: readonly DocCategory[],
  base: string,
): NavDropdownGroup[] {
  return categories
    .map((key) => ({
      label: CATEGORY_LABELS[key],
      items: docsInCategory(docs, key, base),
    }))
    .filter((g) => g.items.length > 0);
}

/** Build navbar items with dropdown groups from the docs collection. */
export function buildMainNav(base: string, docs: DocNavEntry[]): MainNavItem[] {
  return [
    {
      label: "Docs",
      href: `${base}docs/`,
      groups: groupsForCategories(docs, CORE_DOC_CATEGORIES, base),
    },
    {
      label: "Coverage",
      href: `${base}coverage/`,
      groups: [
        {
          label: "Reports",
          items: [{ label: "Python coverage", href: `${base}coverage/` }],
        },
      ],
    },
  ];
}

/** @deprecated Use buildMainNav — kept for tests that only need labels. */
export function mainNavLinks(base: string): { label: string; href: string }[] {
  return buildMainNav(base, []).map(({ label, href }) => ({ label, href }));
}
