/** External services referenced in Cloud and deployment docs. */

export interface ServiceLink {
  id: string;
  name: string;
  url: string;
  description?: string;
}

export const services = {
  railway: {
    id: 'railway',
    name: 'Railway',
    url: 'https://railway.app',
    description: 'Docker deploy hosting',
  },
  neon: {
    id: 'neon',
    name: 'Neon',
    url: 'https://neon.tech',
    description: 'Managed PostgreSQL',
  },
  cloudflareR2: {
    id: 'cloudflare-r2',
    name: 'Cloudflare R2',
    url: 'https://www.cloudflare.com/developer-platform/r2/',
    description: 'Object storage',
  },
  workos: {
    id: 'workos',
    name: 'WorkOS',
    url: 'https://workos.com',
    description: 'AuthKit authentication',
  },
  paddle: {
    id: 'paddle',
    name: 'Paddle',
    url: 'https://developer.paddle.com/',
    description: 'Billing MoR',
  },
  grafana: {
    id: 'grafana',
    name: 'Grafana Cloud',
    url: 'https://grafana.com/products/cloud/',
    description: 'Metrics dashboards',
  },
  sentry: {
    id: 'sentry',
    name: 'Sentry',
    url: 'https://sentry.io',
    description: 'Error tracking',
  },
  terraform: {
    id: 'terraform',
    name: 'Terraform',
    url: 'https://www.terraform.io',
    description: 'Infrastructure as code',
  },
  lgtmCi: {
    id: 'lgtm-ci',
    name: 'lgtm-ci',
    url: 'https://github.com/lgtm-hq/lgtm-ci',
    description: 'Reusable CI workflows',
  },
  ghcr: {
    id: 'ghcr',
    name: 'GHCR',
    url: 'https://github.com/lgtm-hq/rustume/pkgs/container/rustume',
    description: 'Container registry',
  },
  cosign: {
    id: 'cosign',
    name: 'cosign',
    url: 'https://docs.sigstore.dev/cosign/overview/',
    description: 'Image signing',
  },
  osvScanner: {
    id: 'osv-scanner',
    name: 'OSV-Scanner',
    url: 'https://google.github.io/osv-scanner/',
    description: 'Vulnerability scanning',
  },
  pipAudit: {
    id: 'pip-audit',
    name: 'pip-audit',
    url: 'https://github.com/pypa/pip-audit',
    description: 'Python dependency vulnerability scanning',
  },
} as const satisfies Record<string, ServiceLink>;
