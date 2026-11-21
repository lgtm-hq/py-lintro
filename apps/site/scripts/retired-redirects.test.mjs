import { runInNewContext } from 'node:vm';
import { expect, it } from 'vitest';
import { enhanceRetiredRedirect } from './retired-redirects.mjs';

it.each(['', '/py-lintro'])('keeps query and fragment through the %s redirect', (base) => {
  const destination = `${base}/docs/guides/configuration/`;
  const html = enhanceRetiredRedirect({
    html: `<title>Moved</title><meta http-equiv="refresh" content="0;url=${destination}"><a href="${destination}">Continue</a>`,
    destination,
  });
  let actual;
  runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], {
    URL,
    location: {
      origin: 'https://example.org',
      search: '?from=bookmark',
      hash: '#bandit-configuration',
      replace: (value) => {
        actual = value;
      },
    },
  });
  expect(actual).toBe(`https://example.org${destination}?from=bookmark#bandit-configuration`);
  expect(html).toContain(
    `<noscript><meta http-equiv="refresh" content="0;url=${destination}"></noscript>`
  );
  expect(html).toContain(`<a href="${destination}">Continue</a>`);
});

it('fails the build if the generated document is not a static redirect', () => {
  expect(() =>
    enhanceRetiredRedirect({ html: '<h1>Real page</h1>', destination: '/docs/' })
  ).toThrow('Missing static redirect');
});
