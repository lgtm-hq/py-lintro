import { visit } from 'unist-util-visit';

/**
 * Wrap every rendered markdown table in a scrolling container.
 *
 * Wide tables then scroll horizontally inside `.table-scroll` while the table
 * itself keeps `display: table`, so assistive technology still sees a table.
 * The wrapper is focusable and named as a group (not a landmark, since a page
 * may hold many tables) so keyboard users can reach and scroll it.
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
        properties: {
          className: ['table-scroll'],
          tabIndex: 0,
          role: 'group',
          ariaLabel: 'Scrollable table',
        },
        children: [node],
      };
    });
  };
}
