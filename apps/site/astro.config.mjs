// @ts-check
import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import sitemap from '@astrojs/sitemap';
import { rehypeSiteImages } from './src/lib/rehype-site-images.mjs';
import { remarkUnwrapCrossPageLinks } from './src/lib/remark-unwrap-cross-page-links.mjs';
import {
  rehypeDocLinks,
  rehypeUnwrapCrossPageLinks,
  rehypeUnwrapHeadingLinks,
} from './src/lib/rehype-doc-links.mjs';

const base = process.env.ASTRO_BASE || '/';

/** @type {import('astro').AstroUserConfig} */
export default defineConfig({
  site: 'https://lgtm-hq.github.io',
  base,
  output: 'static',
  integrations: [sitemap()],
  markdown: {
    processor: unified({
      remarkPlugins: [remarkUnwrapCrossPageLinks],
      rehypePlugins: [
        [rehypeSiteImages, base],
        rehypeUnwrapHeadingLinks,
        rehypeUnwrapCrossPageLinks,
        [rehypeDocLinks, base],
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
