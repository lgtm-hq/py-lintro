// @ts-check
import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import sitemap from '@astrojs/sitemap';
import { rehypeSiteImages } from './src/lib/rehype-site-images.mjs';
import { rehypeTableScroll } from './src/lib/rehype-table-scroll.mjs';
import { rehypeDocLinks, rehypeUnwrapHeadingLinks } from './src/lib/rehype-doc-links.mjs';

const base = process.env.ASTRO_BASE || '/';

/**
 * Routes published by the previous site layout, mapped to their new homes so
 * bookmarks and inbound links keep resolving. Tool pages kept their paths.
 */
const retiredDocRoutes = {
  '/docs/getting-started/hub/': '/docs/start/overview/',
  '/docs/getting-started/getting-started/': '/docs/start/getting-started/',
  '/docs/usage/': '/docs/guides/',
  '/docs/usage/configuration/': '/docs/guides/configuration/',
  '/docs/usage/watch-mode/': '/docs/guides/watch-mode/',
  '/docs/usage/docker/': '/docs/guides/docker/',
  '/docs/usage/github-integration/': '/docs/guides/github-integration/',
  '/docs/usage/library-api/': '/docs/guides/library-api/',
  '/docs/usage/troubleshooting/': '/docs/guides/troubleshooting/',
  '/docs/usage/debugging/': '/docs/guides/debugging/',
  '/docs/usage/ai-features/': '/docs/ai/ai-features/',
  '/docs/usage/ai-review-transports/': '/docs/ai/review-transports/',
  '/docs/usage/plugins/': '/docs/contribute/plugins/',
  '/docs/contributing/contributing/': '/docs/contribute/',
  '/docs/contributing/style-guide/': '/docs/contribute/style-guide/',
  '/docs/contributing/shell-script-style-guide/': '/docs/contribute/shell-script-style-guide/',
  '/docs/contributing/lintro-self-use/': '/docs/contribute/self-use/',
  '/docs/architecture/overview/': '/docs/project/',
  '/docs/architecture/architecture/': '/docs/project/architecture/',
  '/docs/architecture/vision/': '/docs/project/vision/',
  '/docs/architecture/roadmap/': '/docs/project/roadmap/',
  '/docs/architecture/ai-review-execution/': '/docs/ai/review-execution/',
  '/docs/security/': '/docs/project/security/',
  '/docs/security/assurance/': '/docs/project/security/assurance/',
  '/docs/security/requirements/': '/docs/project/security/requirements/',
};

/** @type {import('astro').AstroUserConfig} */
export default defineConfig({
  site: 'https://lgtm-hq.github.io',
  base,
  output: 'static',
  integrations: [sitemap()],
  redirects: Object.fromEntries(
    Object.entries(retiredDocRoutes).map(([from, to]) => [from, `${base.replace(/\/$/, '')}${to}`])
  ),
  markdown: {
    processor: unified({
      rehypePlugins: [
        [rehypeSiteImages, base],
        rehypeUnwrapHeadingLinks,
        [rehypeDocLinks, base],
        rehypeTableScroll,
      ],
    }),
    shikiConfig: { theme: 'css-variables', wrap: true },
  },
  build: { format: 'directory' },
  vite: {
    build: {
      target: 'esnext',
      assetsInlineLimit: 0,
    },
  },
});
