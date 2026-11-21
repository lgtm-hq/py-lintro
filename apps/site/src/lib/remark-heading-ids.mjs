import { visit } from 'unist-util-visit';

/** Honor the explicit {#id} anchors already authored in the product docs. */
export function remarkHeadingIds() {
  return (tree) => {
    visit(tree, 'heading', (node) => {
      const last = node.children.at(-1);
      if (last?.type !== 'text') return;
      const match = last.value.match(/\s*\{#([\w—–-]+)\}\s*$/);
      if (!match) return;
      last.value = last.value.slice(0, match.index);
      node.data ??= {};
      node.data.hProperties ??= {};
      // Smart punctuation may run before this plugin in Astro's unified pipeline.
      node.data.hProperties.id = match[1].replaceAll('—', '--').replaceAll('–', '-');
    });
  };
}
