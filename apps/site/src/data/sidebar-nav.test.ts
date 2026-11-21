import { describe, expect, it } from 'vitest';
import {
  flattenSectionDocs,
  groupSectionDocs,
  prevNextDocs,
  resolveNavGroup,
  sidebarLabel,
} from './sidebar-nav';

const guideDocs = [
  { id: 'guides', data: { title: 'Guides', order: 5 } },
  {
    id: 'guides/configuration',
    data: { title: 'Configuration Guide', navTitle: 'Configuration', navGroup: 'setup', order: 10 },
  },
  {
    id: 'guides/docker',
    data: { title: 'Docker Usage Guide', navTitle: 'Docker', navGroup: 'setup', order: 30 },
  },
  {
    id: 'guides/github-integration',
    data: { title: 'GitHub Integration', navTitle: 'GitHub Actions', navGroup: 'ci', order: 50 },
  },
  {
    id: 'guides/debugging',
    data: { title: 'Debugging', navGroup: 'debug', order: 90 },
  },
];

describe('groupSectionDocs', () => {
  it('groups guides in sidebar order with human labels', () => {
    const { groups } = groupSectionDocs('guides', guideDocs);
    expect(groups.map((g) => g.label)).toEqual(['Setup', 'Run in CI', 'Debug']);
    expect(groups[0]?.docs.map(sidebarLabel)).toEqual(['Configuration', 'Docker']);
  });

  it('excludes the section landing page from grouped pages', () => {
    const { groups } = groupSectionDocs('guides', guideDocs);
    expect(groups.flatMap((g) => g.docs.map((d) => d.id))).not.toContain('guides');

    const start = [
      { id: 'start/overview', data: { title: 'Overview', order: 5 } },
      { id: 'start/getting-started', data: { title: 'Getting started', order: 10 } },
    ];
    expect(groupSectionDocs('start', start).groups[0]?.docs.map((d) => d.id)).toEqual([
      'start/getting-started',
    ]);
  });

  it('groups tools by language without frontmatter', () => {
    const docs = [
      { id: 'tools', data: { title: 'Tools', order: 5 } },
      { id: 'tools/spectral', data: { title: 'spectral', order: 20 } },
      { id: 'tools/stylelint', data: { title: 'stylelint', order: 30 } },
      { id: 'tools/vale', data: { title: 'vale', order: 40 } },
    ];

    const { groups } = groupSectionDocs('tools', docs);
    expect(groups.find((g) => g.key === 'config')?.docs.map((d) => d.id)).toContain(
      'tools/spectral'
    );
    expect(groups.find((g) => g.key === 'js-ts')?.label).toBe('JavaScript & CSS');
    expect(groups.find((g) => g.key === 'docs')?.docs.map((d) => d.id)).toContain('tools/vale');
    expect(groups.some((g) => g.key === 'other')).toBe(false);
  });

  it('lets nested project pages inherit their directory group', () => {
    expect(
      resolveNavGroup({ id: 'project/adr/0003-sarif', data: { title: 'ADR-0003', order: 43 } })
    ).toBe('decisions');
    expect(resolveNavGroup({ id: 'project/adr', data: { title: 'ADRs', order: 40 } })).toBe(
      'decisions'
    );
  });
});

describe('reading order', () => {
  it('flattens a section as landing page first, then groups in order', () => {
    expect(flattenSectionDocs('guides', guideDocs).map((d) => d.id)).toEqual([
      'guides',
      'guides/configuration',
      'guides/docker',
      'guides/github-integration',
      'guides/debugging',
    ]);
  });

  it('computes previous and next pages', () => {
    const { prev, next } = prevNextDocs('guides', guideDocs, 'guides/docker');
    expect(prev?.id).toBe('guides/configuration');
    expect(next?.id).toBe('guides/github-integration');

    expect(prevNextDocs('guides', guideDocs, 'guides').prev).toBeUndefined();
    expect(prevNextDocs('guides', guideDocs, 'guides/debugging').next).toBeUndefined();
    expect(prevNextDocs('guides', guideDocs, 'missing')).toEqual({});
  });
});
