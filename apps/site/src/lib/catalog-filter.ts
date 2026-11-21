import { TOOLS } from '../data/tools-catalog';
export { matchesCatalog, type CatalogFilters } from './catalog-matcher';

const featured = [
  'ruff',
  'black',
  'mypy',
  'bandit',
  'oxlint',
  'prettier',
  'tsc',
  'shellcheck',
  'shfmt',
  'hadolint',
  'gitleaks',
  'osv-scanner',
];
/** Give the initial sample a useful cross-section; search still includes every tool. */
export const catalogTools = [...TOOLS].sort((left, right) => {
  const a = featured.indexOf(left.name);
  const b = featured.indexOf(right.name);
  return (a < 0 ? featured.length : a) - (b < 0 ? featured.length : b);
});
