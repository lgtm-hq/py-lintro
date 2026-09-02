import { type DocCategory, isSectionOverview, sectionOverviewIds } from './docs-nav';

/** Sidebar group order inside each section. */
export const SECTION_GROUP_ORDER: Partial<Record<DocCategory, readonly string[]>> = {
  start: ['start', 'evaluate'],
  guides: ['setup', 'ci', 'distribute', 'debug'],
  ai: ['features', 'review', 'agents', 'internals'],
  tools: ['python', 'js-ts', 'rust', 'go', 'frameworks', 'docs', 'config', 'ci-ops', 'security'],
  contribute: ['develop', 'standards', 'practices'],
  project: ['architecture', 'decisions', 'design', 'security'],
};

export const SECTION_GROUP_LABELS: Partial<Record<DocCategory, Record<string, string>>> = {
  start: { start: 'Start here', evaluate: 'Evaluate' },
  guides: {
    setup: 'Setup',
    ci: 'Run in CI',
    distribute: 'Distribute',
    debug: 'Debug',
  },
  ai: {
    features: 'Features',
    review: 'Code review',
    agents: 'Agents',
    internals: 'Internals',
  },
  tools: {
    python: 'Python',
    'js-ts': 'JavaScript & CSS',
    rust: 'Rust',
    go: 'Go',
    frameworks: 'Frameworks',
    docs: 'Docs & prose',
    config: 'Config & schemas',
    'ci-ops': 'CI & containers',
    security: 'Security',
  },
  contribute: {
    develop: 'Develop',
    standards: 'Standards',
    practices: 'Practices',
  },
  project: {
    architecture: 'Architecture',
    decisions: 'Decision records',
    design: 'Design notes',
    security: 'Security',
  },
};

/** Fallback nav groups by doc id when frontmatter omits navGroup. */
export const NAV_GROUP_BY_ID: Partial<Record<string, string>> = {
  'start/getting-started': 'start',
  'start/comparison': 'evaluate',
  'guides/configuration': 'setup',
  'guides/watch-mode': 'setup',
  'guides/docker': 'setup',
  'guides/pre-commit': 'ci',
  'guides/github-integration': 'ci',
  'guides/npm-distribution': 'distribute',
  'guides/library-api': 'distribute',
  'guides/troubleshooting': 'debug',
  'guides/debugging': 'debug',
  'ai/ai-features': 'features',
  'ai/review-transports': 'review',
  'ai/review-report': 'review',
  'ai/mcp': 'agents',
  'ai/review-execution': 'internals',
  'tools/ruff': 'python',
  'tools/black': 'python',
  'tools/mypy': 'python',
  'tools/bandit': 'python',
  'tools/pydoclint': 'python',
  'tools/pytest': 'python',
  'tools/idiom-review': 'python',
  'tools/prettier': 'js-ts',
  'tools/tsc': 'js-ts',
  'tools/oxc': 'js-ts',
  'tools/stylelint': 'js-ts',
  'tools/clippy': 'rust',
  'tools/cargo-deny': 'rust',
  'tools/golangci-lint': 'go',
  'tools/astro-check': 'frameworks',
  'tools/svelte-check': 'frameworks',
  'tools/vue-tsc': 'frameworks',
  'tools/html-validate': 'frameworks',
  'tools/markdownlint': 'docs',
  'tools/vale': 'docs',
  'tools/typos': 'docs',
  'tools/yamllint': 'config',
  'tools/buf': 'config',
  'tools/spectral': 'config',
  'tools/dotenv-linter': 'config',
  'tools/actionlint': 'ci-ops',
  'tools/hadolint': 'ci-ops',
  'tools/commitlint': 'ci-ops',
  'tools/osv-scanner': 'security',
  'tools/pip-audit': 'security',
  'tools/trufflehog': 'security',
  'contribute/adding-a-new-tool': 'develop',
  'contribute/testing': 'develop',
  'contribute/plugins': 'develop',
  'contribute/style-guide': 'standards',
  'contribute/shell-script-style-guide': 'standards',
  'contribute/self-use': 'practices',
  'project/architecture': 'architecture',
  'project/vision': 'architecture',
  'project/roadmap': 'architecture',
  'project/adr': 'decisions',
  'project/design': 'design',
  'project/security': 'security',
  'project/security/assurance': 'security',
  'project/security/requirements': 'security',
};

export interface SidebarNavDoc {
  id: string;
  data: {
    title: string;
    navTitle?: string;
    navGroup?: string;
    order: number;
  };
}

export interface SidebarNavGroup<T extends SidebarNavDoc = SidebarNavDoc> {
  key: string;
  label: string;
  docs: T[];
}

export function sidebarLabel(doc: SidebarNavDoc): string {
  return doc.data.navTitle ?? doc.data.title;
}

export function resolveNavGroup(doc: SidebarNavDoc): string | undefined {
  if (doc.data.navGroup) {
    return doc.data.navGroup;
  }
  if (NAV_GROUP_BY_ID[doc.id]) {
    return NAV_GROUP_BY_ID[doc.id];
  }
  // Nested ids (project/adr/0001-…) inherit their parent directory's group.
  const parent = doc.id.includes('/') ? doc.id.slice(0, doc.id.lastIndexOf('/')) : undefined;
  return parent ? NAV_GROUP_BY_ID[parent] : undefined;
}

/** Group a section's pages for the sidebar, excluding the section landing page. */
export function groupSectionDocs<T extends SidebarNavDoc>(
  category: DocCategory,
  docs: T[]
): { groups: SidebarNavGroup<T>[] } {
  const groupOrder = SECTION_GROUP_ORDER[category] ?? [];
  const groupLabels = SECTION_GROUP_LABELS[category] ?? {};

  const pages = docs.filter((d) => !isSectionOverview(d.id, category));
  const byGroup = new Map<string, T[]>();

  for (const doc of pages) {
    const group = resolveNavGroup(doc) ?? 'other';
    const items = byGroup.get(group) ?? [];
    items.push(doc);
    byGroup.set(group, items);
  }

  const orderedKeys =
    groupOrder.length > 0
      ? [
          ...groupOrder.filter((key) => byGroup.has(key)),
          ...[...byGroup.keys()].filter((key) => !groupOrder.includes(key)).sort(),
        ]
      : [...byGroup.keys()].sort();

  const groups: SidebarNavGroup<T>[] = orderedKeys.map((key) => ({
    key,
    label: groupLabels[key] ?? key,
    docs: (byGroup.get(key) ?? []).sort((a, b) => a.data.order - b.data.order),
  }));

  return { groups };
}

/** Reading order for a section: landing page first, then groups in sidebar order. */
export function flattenSectionDocs<T extends SidebarNavDoc>(category: DocCategory, docs: T[]): T[] {
  const overviews = sectionOverviewIds(category)
    .map((id) => docs.find((d) => d.id === id))
    .filter((d): d is T => d !== undefined);
  const { groups } = groupSectionDocs(category, docs);
  const ordered = groups.flatMap((group) => group.docs);
  return [...overviews, ...ordered];
}

export interface PrevNext<T extends SidebarNavDoc> {
  prev?: T;
  next?: T;
}

/** Previous and next pages for `currentId` within a section's reading order. */
export function prevNextDocs<T extends SidebarNavDoc>(
  category: DocCategory,
  docs: T[],
  currentId: string
): PrevNext<T> {
  const ordered = flattenSectionDocs(category, docs);
  const index = ordered.findIndex((d) => d.id === currentId);
  if (index === -1) {
    return {};
  }
  return {
    prev: index > 0 ? ordered[index - 1] : undefined,
    next: index < ordered.length - 1 ? ordered[index + 1] : undefined,
  };
}
