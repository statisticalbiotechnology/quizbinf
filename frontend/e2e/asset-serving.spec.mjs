import { expect, test } from '@playwright/test';

/**
 * How the backend serves the built frontend, asserted against the real server
 * rather than a unit stub — this is the seam where a deploy strands browsers.
 *
 * Angular fingerprints its bundles, and index.html names the exact set
 * belonging to one build. A browser holding a cached index.html from the
 * previous deploy asks for chunks that no longer exist; when the SPA fallback
 * answers those with index.html, the browser reports
 *
 *   Failed to load module script: Expected a JavaScript-or-Wasm module script
 *   but the server responded with a MIME type of "text/html"
 *
 * which says nothing about the actual cause. Two rules prevent it: a missing
 * artefact 404s, and index.html is always revalidated.
 */
test('a missing bundle 404s instead of returning the SPA', async ({ request }) => {
  const res = await request.get('/chunk-DOESNOTEXIST.js');

  expect(res.status(), 'a missing .js must 404, not fall through to index.html').toBe(404);
  expect(res.headers()['content-type'] ?? '').not.toContain('text/html');
});

test('index.html is revalidated, fingerprinted bundles are cached', async ({ request }) => {
  const index = await request.get('/');
  expect(index.status()).toBe(200);
  // Without this a redeploy leaves clients on an index that names dead chunks.
  expect(index.headers()['cache-control'] ?? '').toContain('no-cache');

  // Find a real fingerprinted bundle from the page the server just served.
  const html = await index.text();
  const bundle = html.match(/(?:src|href)="\/?([^"]*-[A-Z0-9]{8,}\.(?:js|css))"/);
  expect(bundle, 'index.html should reference a fingerprinted bundle').not.toBeNull();

  const asset = await request.get('/' + bundle[1].replace(/^\//, ''));
  expect(asset.status()).toBe(200);
  expect(asset.headers()['cache-control'] ?? '').toContain('immutable');
});

test('SPA routes still reach the app', async ({ request }) => {
  // Dotless paths are routes, not files: these must keep getting index.html
  // even though nothing exists on disk under those names.
  for (const route of ['/login', '/teacher', '/s/abc123', '/teacher/session/abc123/join']) {
    const res = await request.get(route);
    expect(res.status(), `${route} should serve the SPA`).toBe(200);
    expect(res.headers()['content-type'] ?? '', route).toContain('text/html');
  }
});
