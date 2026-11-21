import { describe, expect, it } from 'vitest';
import {
  ROADMAP_BACKLOG,
  ROADMAP_HEADINGS,
  ROADMAP_PARKED,
  ROADMAP_PLANNED,
  ROADMAP_VERIFIED,
  formatRoadmapDate,
  roadmapIssueUrl,
} from './roadmap';

describe('roadmap verification record', () => {
  it('carries a real calendar date and prints it without a locale', () => {
    expect(ROADMAP_VERIFIED.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const parsed = new Date(`${ROADMAP_VERIFIED.date}T00:00:00Z`);
    expect(parsed.toISOString().slice(0, 10)).toBe(ROADMAP_VERIFIED.date);
    expect(formatRoadmapDate('2026-11-16')).toBe('16 November 2026');
    expect(formatRoadmapDate('2027-01-05')).toBe('5 January 2027');
  });

  it('refuses a value that is not an ISO calendar date', () => {
    expect(() => formatRoadmapDate('16 November 2026')).toThrow('Not an ISO calendar date');
    expect(() => formatRoadmapDate('2026-13-01')).toThrow('Not an ISO calendar date');
  });
});

describe('roadmap issues', () => {
  it('links each planned epic once, to the Lintro repository', () => {
    const issues = [
      ...ROADMAP_PLANNED.map(({ issue }) => issue),
      ...Object.values(ROADMAP_PARKED),
      ROADMAP_BACKLOG,
    ];
    expect(new Set(issues).size).toBe(issues.length);
    for (const issue of issues) {
      expect(Number.isInteger(issue) && issue > 0).toBe(true);
      expect(roadmapIssueUrl(issue)).toBe(`https://github.com/lgtm-hq/py-lintro/issues/${issue}`);
    }
    expect(ROADMAP_PLANNED.every(({ label }) => label.trim().length > 3)).toBe(true);
  });

  it('keeps the three sections the page and its contents rail share', () => {
    expect(ROADMAP_HEADINGS.map(({ slug }) => slug)).toEqual([
      'available-now',
      'planned-work',
      'parked-proposals',
    ]);
  });
});
