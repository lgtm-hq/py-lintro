/** Resolve Markdown heading semantics without rendering the entire reference corpus. */
import { createMarkdownProcessor } from '@astrojs/markdown-remark';
import { remarkHeadingIds } from '../src/lib/remark-heading-ids.mjs';
const processor = await createMarkdownProcessor({
  remarkPlugins: [remarkHeadingIds],
  syntaxHighlight: false,
});

export async function referenceHeadings(markdown) {
  let fence = null;
  const lines = [];
  for (const line of markdown.split('\n')) {
    const marker = line.match(/^\s{0,3}(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = null;
      continue;
    }
    if (!fence && /^#{1,6}\s/.test(line)) lines.push(line);
  }
  const {
    metadata: { headings },
  } = await processor.render(lines.join('\n\n'));
  return headings;
}
