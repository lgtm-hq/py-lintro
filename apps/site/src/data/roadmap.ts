/** Current roadmap sections shared by the article and its static navigation. */
export const ROADMAP_HEADINGS = [
  { depth: 2, slug: 'available-now', text: 'Available now' },
  { depth: 2, slug: 'planned-work', text: 'Planned work' },
  { depth: 2, slug: 'parked-proposals', text: 'Parked proposals' },
] as const;

const ISSUES = 'https://github.com/lgtm-hq/py-lintro/issues/';

/** Link to a GitHub issue in the Lintro repository. */
export const roadmapIssueUrl = (issue: number): string => `${ISSUES}${issue}`;

/**
 * When the issues below were last read back from GitHub as open, and the Lintro
 * release the roadmap page described at the time. The page prints both as historical provenance. A build must not
 * advance this record without a fresh review of the issues.
 */
export const ROADMAP_VERIFIED = {
  date: '2026-11-16',
  lintroVersion: '0.180.34',
} as const;

/** Open epics the roadmap page presents as planned work. */
export const ROADMAP_PLANNED = [
  { label: 'Tool and install correctness', issue: 3309 },
  { label: 'Code health and standards', issue: 2287 },
  { label: 'CLI output and user experience', issue: 3310 },
  { label: 'AI review hardening', issue: 2555 },
  { label: 'Tool ordering and verification', issue: 1735 },
  { label: 'CI reliability', issue: 2245 },
  { label: 'Docs and site', issue: 1917 },
] as const;

/** Epics that collect proposals with no commitment. */
export const ROADMAP_PARKED = {
  tools: 3313,
  features: 3314,
} as const;

/** The ordered backlog that is the planning source of record. */
export const ROADMAP_BACKLOG = 2288;

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
] as const;

/**
 * Render an ISO date as "16 November 2026" without depending on the build locale.
 * A value that is not `YYYY-MM-DD` fails the build instead of printing "undefined".
 */
export function formatRoadmapDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const month = match ? MONTHS[Number(match[2]) - 1] : undefined;
  if (!match || !month) throw new Error(`Not an ISO calendar date: ${iso}`);
  return `${Number(match[3])} ${month} ${match[1]}`;
}
