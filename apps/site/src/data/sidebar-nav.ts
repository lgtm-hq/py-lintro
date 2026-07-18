import { SECTION_OVERVIEW_ID, type DocCategory } from './docs-nav';

export const SECTION_GROUP_ORDER: Partial<Record<DocCategory, readonly string[]>> = {
  'getting-started': ['start'],
  usage: ['setup', 'ci', 'extend'],
  tools: ['python', 'js-ts', 'rust', 'frameworks', 'config', 'ci-ops', 'security'],
  architecture: ['design'],
  security: ['policy'],
  contributing: ['standards', 'meta'],
};

export const SECTION_GROUP_LABELS: Partial<Record<DocCategory, Record<string, string>>> = {
  'getting-started': { start: 'start here' },
  usage: {
    setup: 'setup',
    ci: 'ci',
    extend: 'debug',
  },
  tools: {
    python: 'python',
    'js-ts': 'js / ts',
    rust: 'rust',
    frameworks: 'frameworks',
    config: 'config',
    'ci-ops': 'ci & ops',
    security: 'security',
  },
  architecture: { design: 'design' },
  security: { policy: 'policy' },
  contributing: {
    standards: 'standards',
    meta: 'practices',
  },
};

/** Fallback nav groups by doc id when frontmatter omits navGroup. */
export const NAV_GROUP_BY_ID: Partial<Record<string, string>> = {
  'getting-started/getting-started': 'start',
  'usage/configuration': 'setup',
  'usage/docker': 'setup',
  'usage/github-integration': 'ci',
  'usage/ai-features': 'extend',
  'usage/troubleshooting': 'extend',
  'usage/debugging': 'extend',
  'usage/plugins': 'extend',
  'tools/ruff': 'python',
  'tools/black': 'python',
  'tools/mypy': 'python',
  'tools/bandit': 'python',
  'tools/pydoclint': 'python',
  'tools/pytest': 'python',
  'tools/prettier': 'js-ts',
  'tools/tsc': 'js-ts',
  'tools/oxc': 'js-ts',
  'tools/clippy': 'rust',
  'tools/cargo-deny': 'rust',
  'tools/astro-check': 'frameworks',
  'tools/svelte-check': 'frameworks',
  'tools/vue-tsc': 'frameworks',
  'tools/yamllint': 'config',
  'tools/markdownlint': 'config',
  'tools/actionlint': 'ci-ops',
  'tools/hadolint': 'ci-ops',
  'tools/osv-scanner': 'security',
  'tools/pip-audit': 'security',
  'architecture/architecture': 'design',
  'architecture/vision': 'design',
  'architecture/roadmap': 'design',
  'security/requirements': 'policy',
  'security/assurance': 'policy',
  'contributing/style-guide': 'standards',
  'contributing/shell-script-style-guide': 'standards',
  'contributing/lintro-self-use': 'meta',
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

export interface SidebarNavGroup {
  key: string;
  label: string;
  docs: SidebarNavDoc[];
}

export function sidebarLabel(doc: SidebarNavDoc): string {
  return doc.data.navTitle ?? doc.data.title;
}

export function resolveNavGroup(doc: SidebarNavDoc): string | undefined {
  if (doc.data.navGroup) {
    return doc.data.navGroup;
  }
  return NAV_GROUP_BY_ID[doc.id];
}

export function sectionOverviewId(category: DocCategory): string {
  return SECTION_OVERVIEW_ID[category];
}

export function groupSectionDocs(
  category: DocCategory,
  docs: SidebarNavDoc[]
): { groups: SidebarNavGroup[] } {
  const overviewId = SECTION_OVERVIEW_ID[category];
  const groupOrder = SECTION_GROUP_ORDER[category] ?? [];
  const groupLabels = SECTION_GROUP_LABELS[category] ?? {};

  const pages = docs.filter((d) => d.id !== overviewId);
  const byGroup = new Map<string, SidebarNavDoc[]>();

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

  const groups: SidebarNavGroup[] = orderedKeys.map((key) => ({
    key,
    label: groupLabels[key] ?? key,
    docs: (byGroup.get(key) ?? []).sort((a, b) => a.data.order - b.data.order),
  }));

  return { groups };
}
