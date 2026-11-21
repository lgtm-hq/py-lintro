import { describe, expect, it } from 'vitest';
import { rehype } from 'rehype';
import rehypeStringify from 'rehype-stringify';
import { rehypeTableScroll } from './rehype-table-scroll.mjs';

/**
 * @param {string} html
 * @returns {Promise<string>}
 */
async function transform(html) {
  const file = await rehype()
    .data('settings', { fragment: true })
    .use(rehypeTableScroll)
    .use(rehypeStringify)
    .process(html);
  return String(file);
}

describe('rehypeTableScroll', () => {
  it('wraps a table in a scrolling container', async () => {
    const output = await transform('<table><tr><td>a</td></tr></table>');
    expect(output).toBe(
      '<div class="table-scroll" tabindex="0" role="group" aria-label="Scrollable table"><table><tbody><tr><td>a</td></tr></tbody></table></div>'
    );
  });

  it('does not double-wrap an already wrapped table', async () => {
    const input =
      '<div class="table-scroll"><table><tbody><tr><td>a</td></tr></tbody></table></div>';
    expect(await transform(input)).toBe(input);
  });

  it('leaves other elements alone', async () => {
    expect(await transform('<p>text</p>')).toBe('<p>text</p>');
  });
});
