/** Canonical internal paths (relative to site base, trailing slash). */
export const docs = {
  hub: 'docs/getting-started/hub/',
  gettingStarted: 'docs/getting-started/getting-started/',
  configuration: 'docs/usage/configuration/',
  docker: 'docs/usage/docker/',
  githubIntegration: 'docs/usage/github-integration/',
  aiFeatures: 'docs/usage/ai-features/',
  contributing: 'docs/contributing/contributing/',
  architectureOverview: 'docs/architecture/overview/',
  toolsIndex: 'docs/tools/',
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
