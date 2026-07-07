import { describe, expect, it } from 'vitest';
import { groupSectionDocs, sidebarLabel } from './sidebar-nav';

describe('groupSectionDocs', () => {
  it('groups usage docs with short nav labels', () => {
    const docs = [
      {
        id: 'usage',
        data: { title: 'usage', order: 5 },
      },
      {
        id: 'usage/configuration',
        data: { title: 'configuration', navTitle: 'configuration', navGroup: 'setup', order: 20 },
      },
      {
        id: 'usage/docker',
        data: { title: 'docker', navTitle: 'docker', navGroup: 'setup', order: 30 },
      },
      {
        id: 'usage/github-integration',
        data: { title: 'github', navTitle: 'github', navGroup: 'ci', order: 40 },
      },
    ];

    const { groups } = groupSectionDocs('usage', docs);
    expect(groups).toHaveLength(2);
    expect(groups[0]?.label).toBe('setup');
    expect(groups[0]?.docs.map(sidebarLabel)).toEqual(['configuration', 'docker']);
  });

  it('excludes section overview from grouped pages', () => {
    const docs = [
      { id: 'getting-started/hub', data: { title: 'hub', order: 5 } },
      {
        id: 'getting-started/getting-started',
        data: { title: 'getting started', navGroup: 'start', order: 10 },
      },
    ];

    const { groups } = groupSectionDocs('getting-started', docs);
    expect(groups[0]?.docs.map((d) => d.id)).toEqual(['getting-started/getting-started']);
  });

  it('keeps tools overview out of tool groups', () => {
    const docs = [
      { id: 'tools', data: { title: 'tools', order: 5 } },
      {
        id: 'tools/ruff',
        data: { title: 'ruff', navGroup: 'python', order: 20 },
      },
    ];

    const { groups } = groupSectionDocs('tools', docs);
    expect(groups[0]?.docs[0]?.id).toBe('tools/ruff');
  });
});
