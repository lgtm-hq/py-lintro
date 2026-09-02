import { visit } from 'unist-util-visit';

/**
 * Wrap every rendered markdown table in a scrolling container.
 *
 * Wide tables then scroll horizontally inside `.table-scroll` while the table
 * itself keeps `display: table`, so assistive technology still sees a table.
 *
 * @returns {(tree: import('hast').Root) => void}
 */
export function rehypeTableScroll() {
  return (tree) => {
    visit(tree, 'element', (node, index, parent) => {
      if (node.tagName !== 'table' || !parent || index === undefined) return;
      if (parent.type === 'element' && parent.properties?.className?.includes?.('table-scroll')) {
        return;
      }
      parent.children[index] = {
        type: 'element',
        tagName: 'div',
        properties: { className: ['table-scroll'] },
        children: [node],
      };
    });
  };
}
