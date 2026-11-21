import { readFile, writeFile } from 'node:fs/promises';

/** Preserve incoming bookmarks before the static meta-refresh fallback runs. */
export function enhanceRetiredRedirect({ html, destination }) {
  const refresh = /<meta\b(?=[^>]*http-equiv=["']refresh["'])[^>]*>/i;
  if (!refresh.test(html)) throw new Error(`Missing static redirect: ${destination}`);
  const encoded = JSON.stringify(destination).replaceAll('<', '\\u003c');
  const script = `<script>(()=>{const target=new URL(${encoded},location.origin);target.search=location.search;target.hash=location.hash;location.replace(target.href)})()</script>`;
  return html.replace(refresh, (meta) => `${script}<noscript>${meta}</noscript>`);
}

/** Enhance Astro's generated retired routes without maintaining a second route map. */
export function preserveRetiredFragments({ routes, basePath }) {
  return {
    name: 'preserve-retired-fragments',
    hooks: {
      'astro:build:done': async ({ dir }) => {
        await Promise.all(
          Object.entries(routes).map(async ([from, to]) => {
            const file = new URL(`${from.replace(/^\//, '')}index.html`, dir);
            const html = await readFile(file, 'utf8');
            await writeFile(
              file,
              enhanceRetiredRedirect({ html, destination: `${basePath}${to}` })
            );
          })
        );
      },
    },
  };
}
