/** Canonical internal paths (relative to site base, trailing slash). */
export const docs = {
  hub: 'docs/',
  overview: 'docs/start/overview/',
  gettingStarted: 'docs/start/getting-started/',
  comparison: 'docs/start/comparison/',
  configuration: 'docs/guides/configuration/',
  watchMode: 'docs/guides/watch-mode/',
  docker: 'docs/guides/docker/',
  preCommit: 'docs/guides/pre-commit/',
  githubIntegration: 'docs/guides/github-integration/',
  npmDistribution: 'docs/guides/npm-distribution/',
  aiIndex: 'docs/ai/',
  aiFeatures: 'docs/ai/ai-features/',
  aiReviewTransports: 'docs/ai/review-transports/',
  aiReviewReport: 'docs/ai/review-report/',
  mcp: 'docs/ai/mcp/',
  toolsIndex: 'docs/tools/',
  contributing: 'docs/contribute/',
  plugins: 'docs/contribute/plugins/',
  architectureOverview: 'docs/project/',
  security: 'docs/project/security/',
  coverage: 'coverage/',
} as const;

export const home = {
  label: 'Lintro',
  href: '/',
} as const;

export const external = {
  github: {
    label: 'GitHub',
    href: 'https://github.com/lgtm-hq/py-lintro',
  },
  pypi: {
    label: 'PyPI',
    href: 'https://pypi.org/project/lintro/',
  },
  npm: {
    label: 'npm',
    href: 'https://www.npmjs.com/package/@lgtm-hq/lintro',
  },
  docker: {
    label: 'Docker',
    href: 'https://github.com/lgtm-hq/py-lintro/pkgs/container/py-lintro',
  },
  codecov: {
    label: 'Codecov',
    href: 'https://codecov.io/gh/lgtm-hq/py-lintro',
  },
  python: {
    label: 'Python',
    href: 'https://www.python.org/',
  },
} as const;

export function docHref(base: string, path: string): string {
  const trimmedBase = base.trim().replace(/\/+$/, '');
  const normalizedBase =
    trimmedBase === '' || trimmedBase === '/'
      ? '/'
      : `${trimmedBase.startsWith('/') ? trimmedBase : `/${trimmedBase}`}/`;
  const normalizedPath = path.trim().replace(/^\/+/, '').replace(/\/+/g, '/');
  return normalizedPath.length > 0 ? `${normalizedBase}${normalizedPath}` : normalizedBase;
}
